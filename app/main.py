# app/main.py
"""
FastAPI — Monitor de Ajuste Presupuestario (MAP) v2.2.0
Endpoints:
  GET  /                                → Health JSON (para el dashboard)
  GET  /dashboard                       → Sirve main.html
  GET  /api/v1/partidas/                → Listado con filtros
  GET  /api/v1/analisis/ranking         → Top N partidas más ajustadas
  GET  /api/v1/analisis/por-inciso      → Ajuste agregado por inciso
  GET  /api/v1/analisis/evolucion-real  → Evolución gasto real por año
  GET  /api/v1/analisis/sector          → Comparativa sectorial 2023→2026 ✅
  GET  /api/v1/macro/series             → IPC y USD desde BCRA
  GET  /api/v1/macro/base-monetaria     → Base Monetaria en vivo BCRA
  GET  /api/v1/normativa/               → Listado de normas JGM
  GET  /api/v1/normativa/{id}           → Detalle de una norma
  GET  /api/v1/normativa/{id}/partidas  → Partidas afectadas por norma
  GET  /api/v1/comparativa/             → Gasto nominal vs real vs inflación vs USD
  POST /api/v1/scrape/trigger           → Dispara scraper BORA (async)
  GET  /health                          → Health check
"""
import asyncio
import uvicorn
import httpx
from datetime import datetime, date
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from typing import List, Optional, Dict

from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, text

from app.database import models, schemas
from app.database.session import SessionLocal, engine
from app.core.engine import AnalizadorPresupuestario, cargar_macro_indices
from app.core.viz import generar_grafico_ajuste

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Monitor de Ajuste Presupuestario (MAP)",
    description=(
        "Análisis del ajuste presupuestario 2023-2026: "
        "partidas vs IPC y cotización del dólar."
    ),
    version="2.2.0",
)

# ── Archivos estáticos ─────────────────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── DB dependency ──────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# MAPEO SECTORIAL DEFINITIVO — verificado contra sql_app.db
# Diagnóstico: _check2.py a _check7.py — mayo 2026
#
# HALLAZGOS CLAVE:
#   - jur 77 (Infraestructura) solo tiene datos de 2024, NO de 2026
#   - Obra pública 2026 quedó en jur 50 (Ministerio de Economía), programas específicos
#   - Jubilaciones 2023: jur 75 (Trabajo) prg 16,17,21,30,31
#   - Jubilaciones 2026: jur 88 (Capital Humano) prg 16,17,21,30,31
#   - Capital Humano 2026: jur 88 agrupa Educación (jur 70) + Trabajo (jur 75) + Des.Social (jur 85)
#   - Salud: jur 80 sin cambios en ambos períodos
#   - Tabla real: presupuesto_base (NO "partidas")
#   - Campos: monto_original (2023), monto_vigente (2026)
#   - Años disponibles: 2023, 2024, 2025, 2026
# ══════════════════════════════════════════════════════════════════════════════

SECTORES: Dict[str, dict] = {

    "obra_publica": {
        "label": "Obra Pública / Infraestructura",
        "icon": "🏗️",
        "color": "#1a3a6e",
        # 2023: Obras Públicas (64) + Transporte (57) + Hábitat (65)
        "jur_2023": [64, 57, 65],
        "prg_2023": None,           # toda la jurisdicción es obra pública
        # 2026: migró a jur 50 (Economía) — solo programas de infraestructura/vial
        # Excluye energía (prg 74,73,75), economía general (prg 45,18,36,etc.)
        "jur_2026": [50],
        "prg_2026": [62, 63, 48, 51, 82, 54, 16, 37, 69, 57, 5, 15, 52],
    },

    "jubilaciones": {
        "label": "Jubilaciones y Pensiones",
        "icon": "👴",
        "color": "#3d1e6e",
        # 2023: jur 75 (Trabajo/ANSES) prg previsionales principales
        "jur_2023": [75],
        "prg_2023": [16, 17, 21, 30, 31],
        # 2026: jur 88 (Capital Humano) mismos programas previsionales
        "jur_2026": [88],
        "prg_2026": [16, 17, 21, 30, 31],
    },

    "jubilaciones_fuerzas": {
        "label": "Jubilaciones Fuerzas Armadas y Seguridad",
        "icon": "🛡️",
        "color": "#6b7280",
        # Sin cambio de jurisdicción — jur 41 (Seguridad) y jur 45 (Defensa)
        "jur_2023": [41, 45],
        "prg_2023": [16, 18, 19, 20, 21, 22],
        "jur_2026": [41, 45],
        "prg_2026": [16, 18, 19, 20, 21, 22],
    },

    "capital_humano": {
        "label": "Capital Humano (Educ + Niñez + Empleo)",
        "icon": "📚",
        "color": "#7a4500",
        # 2023: Educación (70) + Trabajo/empleo (75 excl. previsional) + Des.Social (85)
        "jur_2023": [70, 75, 85],
        "prg_2023": None,           # toda la jur — se excluye vía prg_excluir_2023
        # Excluir de jur 75/2023 los programas previsionales (ya en "jubilaciones")
        "prg_excluir_2023": {75: [16, 17, 21, 30, 31]},
        # 2026: todo jur 88 excepto programas previsionales y obra pública
        "jur_2026": [88],
        "prg_2026": None,
        "prg_excluir_2026": {88: [16, 17, 21, 30, 31]},  # excluir previsionales
    },

    "salud": {
        "label": "Salud",
        "icon": "🏥",
        "color": "#145a2a",
        # Sin cambio — jur 80 estable en ambos períodos
        "jur_2023": [80],
        "prg_2023": None,
        "jur_2026": [80],
        "prg_2026": None,
    },

    "seguridad": {
        "label": "Seguridad (Fuerzas Federales)",
        "icon": "🚔",
        "color": "#dc2626",
        # Sin cambio de jurisdicción
        "jur_2023": [41],
        "prg_2023": None,
        "jur_2026": [41],
        "prg_2026": None,
    },

    "defensa": {
        "label": "Defensa Nacional",
        "icon": "⚔️",
        "color": "#374151",
        "jur_2023": [45],
        "prg_2023": None,
        "jur_2026": [45],
        "prg_2026": None,
    },
}

# IPC acumulado enero 2023 → 2026 (fallback — actualizar mensualmente)
# Fuente: INDEC
IPC_FACTOR_FALLBACK = 10.53   # ~953% acumulado ene-2023 → dic-2025
TC_USD_INICIO_2023  = 187.0   # $/USD enero 2023 (TC oficial BNA)
TC_USD_FALLBACK     = 1200.0  # $/USD vigente (actualizar)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — queries sobre presupuesto_base
# La tabla real es "presupuesto_base", campos monto_original / monto_vigente
# Año se guarda en columna "ejercicio" (int)
# ══════════════════════════════════════════════════════════════════════════════

def _sumar_presupuesto(
    db: Session,
    jurisdicciones: List[int],
    ejercicio: int,
    programas: Optional[List[int]] = None,
    prg_excluir: Optional[Dict[int, List[int]]] = None,
) -> float:
    """
    Suma monto_original (si ejercicio==2023) o monto_vigente (resto)
    desde presupuesto_base filtrando por jurisdiccion_id y opcionalmente programa_id.
    """
    campo = "monto_original" if ejercicio == 2023 else "monto_vigente"

    # Construir filtro de jurisdicciones
    jur_in = ", ".join(str(j) for j in jurisdicciones)

    # Filtro de programas (lista de permitidos)
    prg_clause = ""
    if programas:
        prg_in = ", ".join(str(p) for p in programas)
        prg_clause = f" AND programa_id IN ({prg_in})"

    # Filtro de exclusión por jur+programa
    excl_clauses = []
    if prg_excluir:
        for jur_id, prgs in prg_excluir.items():
            if prgs and jur_id in jurisdicciones:
                prg_excl_in = ", ".join(str(p) for p in prgs)
                excl_clauses.append(
                    f"NOT (jurisdiccion_id = {jur_id} AND programa_id IN ({prg_excl_in}))"
                )
    excl_clause = (" AND " + " AND ".join(excl_clauses)) if excl_clauses else ""

    sql = text(f"""
        SELECT COALESCE(SUM({campo}), 0)
        FROM presupuesto_base
        WHERE ejercicio = :ejercicio
          AND jurisdiccion_id IN ({jur_in})
          {prg_clause}
          {excl_clause}
    """)

    resultado = db.execute(sql, {"ejercicio": ejercicio}).scalar()
    return float(resultado or 0)


def _get_ipc_factor(db: Session) -> float:
    """Factor IPC acumulado desde la DB. Fallback a constante."""
    try:
        filas = (
            db.query(models.MacroIndice)
            .filter(
                and_(
                    models.MacroIndice.tipo == "IPC_variacion_mensual",
                    models.MacroIndice.fecha >= date(2023, 1, 1),
                )
            )
            .order_by(models.MacroIndice.fecha)
            .all()
        )
        if not filas:
            return IPC_FACTOR_FALLBACK
        factor = 1.0
        for f in filas:
            factor *= 1 + (float(f.valor) / 100)
        return factor
    except Exception:
        return IPC_FACTOR_FALLBACK


def _get_tc_usd(db: Session) -> float:
    """Último TC USD disponible en la DB. Fallback a constante."""
    try:
        fila = (
            db.query(models.MacroIndice)
            .filter(models.MacroIndice.tipo == "TC_USD_oficial")
            .order_by(models.MacroIndice.fecha.desc())
            .first()
        )
        return float(fila.valor) if fila else TC_USD_FALLBACK
    except Exception:
        return TC_USD_FALLBACK


# ══════════════════════════════════════════════════════════════════════════════
# ROOT — JSON para el dashboard (kpi-ipc y last-update)
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/", tags=["Home"], include_in_schema=False)
async def root(
    accept: Optional[str] = None,
    db: Session = Depends(get_db),
):
    ipc = _get_ipc_factor(db)
    return {
        "app": "Monitor de Ajuste Presupuestario",
        "version": "2.2.0",
        "factor_ipc_acumulado": round(ipc, 4),
        "servidor_tiempo": datetime.utcnow().isoformat(),
    }


# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.get("/dashboard", tags=["Home"], include_in_schema=False)
async def dashboard():
    """Sirve main.html (dashboard visual)."""
    # Acepta main.html o index.html
    for nombre in ("main.html", "index.html"):
        path = os.path.join(static_dir, nombre)
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse("<h1>Dashboard no encontrado. Copiar main.html a app/static/</h1>")


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT /api/v1/analisis/sector  — MAPEO DEFINITIVO 2023→2026
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/v1/analisis/sector", tags=["Análisis"])
async def analisis_sector(
    sector: Optional[str] = Query(
        None,
        description=(
            "Clave del sector: obra_publica | jubilaciones | jubilaciones_fuerzas | "
            "capital_humano | salud | seguridad | defensa. "
            "Sin valor devuelve todos."
        ),
    ),
    db: Session = Depends(get_db),
):
    """
    Comparativa sectorial 2023 → 2026 usando mapeo correcto de jurisdicciones.

    NO usa programa_id para el cruce (inválido post-fusión ministerial).
    Agrega por jurisdiccion_id + programa_id (cuando corresponde) en cada período.

    Retorna 5 KPIs por sector:
      1. credito_original_2023_mm  — $ nominales enero 2023 (millones)
      2. credito_vigente_2026_mm   — $ nominales vigentes 2026 (millones)
      3. var_nominal_pct           — variación % nominal
      4. var_real_ipc_pct          — variación % deflactada por IPC acumulado
      5. var_real_usd_pct          — variación % en dólares constantes

    Mapeo verificado contra sql_app.db — mayo 2026:
      obra_publica:  jur 64+57+65 (2023) → jur 50 prg específicos (2026)
      jubilaciones:  jur 75 prg 16,17,21,30,31 → jur 88 mismos prg
      capital_humano: jur 70+75+85 (excl. previsional) → jur 88 (excl. previsional)
      salud:         jur 80 → jur 80
      seguridad:     jur 41 → jur 41
      defensa:       jur 45 → jur 45
    """
    if sector and sector not in SECTORES:
        raise HTTPException(
            status_code=404,
            detail=f"Sector '{sector}' no reconocido. Opciones: {list(SECTORES.keys())}",
        )

    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)

    sectores_a_calcular = (
        {sector: SECTORES[sector]} if sector else SECTORES
    )

    resultados = []
    for clave, cfg in sectores_a_calcular.items():

        monto_2023 = _sumar_presupuesto(
            db,
            jurisdicciones   = cfg["jur_2023"],
            ejercicio        = 2023,
            programas        = cfg.get("prg_2023"),
            prg_excluir      = cfg.get("prg_excluir_2023"),
        )

        monto_2026 = _sumar_presupuesto(
            db,
            jurisdicciones   = cfg["jur_2026"],
            ejercicio        = 2026,
            programas        = cfg.get("prg_2026"),
            prg_excluir      = cfg.get("prg_excluir_2026"),
        )

        # Variación nominal
        if monto_2023 > 0:
            var_nominal  = (monto_2026 / monto_2023 - 1) * 100
            # Variación real IPC: deflactar el monto 2026
            monto_2026_real = monto_2026 / ipc_factor
            var_real_ipc = (monto_2026_real / monto_2023 - 1) * 100
        else:
            var_nominal  = None
            var_real_ipc = None

        # Variación real USD (TC inicio 2023 vs TC vigente)
        tc_inicio = TC_USD_INICIO_2023
        if monto_2023 > 0 and tc_inicio > 0 and tc_usd > 0:
            monto_2023_usd = monto_2023 / tc_inicio
            monto_2026_usd = monto_2026 / tc_usd
            var_real_usd   = (monto_2026_usd / monto_2023_usd - 1) * 100
        else:
            monto_2023_usd = None
            monto_2026_usd = None
            var_real_usd   = None

        resultados.append({
            "sector":                   clave,
            "label":                    cfg["label"],
            "icon":                     cfg["icon"],
            "color":                    cfg["color"],
            "jur_2023":                 cfg["jur_2023"],
            "jur_2026":                 cfg["jur_2026"],
            "prg_2023":                 cfg.get("prg_2023"),
            "prg_2026":                 cfg.get("prg_2026"),
            "credito_original_2023_mm": round(monto_2023 / 1e6, 1),
            "credito_vigente_2026_mm":  round(monto_2026 / 1e6, 1),
            "var_nominal_pct":          round(var_nominal,  1) if var_nominal  is not None else None,
            "var_real_ipc_pct":         round(var_real_ipc, 1) if var_real_ipc is not None else None,
            "var_real_usd_pct":         round(var_real_usd, 1) if var_real_usd is not None else None,
            "credito_2023_usd_mm":      round(monto_2023_usd / 1e6, 1) if monto_2023_usd else None,
            "credito_2026_usd_mm":      round(monto_2026_usd / 1e6, 1) if monto_2026_usd else None,
            "ipc_factor":               round(ipc_factor, 4),
            "tc_usd":                   round(tc_usd, 2),
        })

    return {
        "generado_en":          datetime.utcnow().isoformat(),
        "ipc_factor_acumulado": round(ipc_factor, 4),
        "tc_usd_vigente":       round(tc_usd, 2),
        "tc_usd_inicio_2023":   TC_USD_INICIO_2023,
        "advertencia_mapeo": (
            "Obra pública 2026 en jur 50 (Economía) prg específicos. "
            "jur 77 solo tiene datos 2024. "
            "Jubilaciones: jur 75→88. Capital Humano: jur 70+75+85→88."
        ),
        "sectores": resultados,
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS EXISTENTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/partidas/", tags=["Partidas"])
async def listar_partidas(
    jurisdiccion_id: Optional[int] = None,
    anio: Optional[int] = None,
    inciso: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Listado de partidas con filtros opcionales."""
    q = db.query(models.Partida)
    if jurisdiccion_id:
        q = q.filter(models.Partida.jurisdiccion_id == jurisdiccion_id)
    if anio:
        q = q.filter(models.Partida.anio == anio)
    if inciso:
        q = q.filter(models.Partida.inciso == inciso)
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"total": total, "skip": skip, "limit": limit, "items": items}


@app.get("/api/v1/analisis/ranking", tags=["Análisis"])
async def ranking_ajuste(
    top_n: int = Query(20, ge=1, le=500, alias="top_n"),
    top: int = Query(20, ge=1, le=500),
    anio_base: int = Query(2023),
    anio_comp: int = Query(2026),
    db: Session = Depends(get_db),
):
    """
    Top N partidas más ajustadas entre anio_base y anio_comp.
    NOTA: Solo válido para ministerios que NO se fusionaron.
    Para sectores fusionados usar /api/v1/analisis/sector.
    """
    n = top_n if top_n != 20 else top
    analizador = AnalizadorPresupuestario(db)
    ipc_factor = _get_ipc_factor(db)
    resultados = analizador.calcular_ranking(
        top=n,
        anio_base=anio_base,
        anio_comp=anio_comp,
        ipc_factor=ipc_factor,
    )
    return {
        "advertencia": (
            "Cruce por programa_id. Inválido para jur fusionadas (64/57/65/70/75/85→50/88). "
            "Usar /api/v1/analisis/sector para sectores correctos."
        ),
        "ipc_factor": round(ipc_factor, 4),
        "ranking": resultados,
    }


@app.get("/api/v1/analisis/por-inciso", tags=["Análisis"])
async def analisis_por_inciso(
    anio: int = Query(2026),
    db: Session = Depends(get_db),
):
    """Ajuste agregado por inciso presupuestario."""
    analizador = AnalizadorPresupuestario(db)
    return analizador.por_inciso(anio=anio)


@app.get("/api/v1/analisis/evolucion-real", tags=["Análisis"])
async def evolucion_real(
    jurisdiccion_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Evolución del gasto nominal y real ($ constantes ene-2023)
    agrupado por año, para toda la administración o una jurisdicción.
    """
    analizador = AnalizadorPresupuestario(db)
    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)
    return analizador.evolucion_real(
        jurisdiccion_id=jurisdiccion_id,
        ipc_factor=ipc_factor,
        tc_usd=tc_usd,
    )


@app.get("/api/v1/macro/series", tags=["Macro"])
async def macro_series():
    """IPC mensual y TC USD desde BCRA (desde ene-2023)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r_ipc = await client.get(
                "https://api.bcra.gob.ar/estadisticas/v3.0/monetarias",
                params={"idVariable": 27, "desde": "2023-01-01"},
            )
            ipc_data = r_ipc.json() if r_ipc.status_code == 200 else []
        except Exception:
            ipc_data = []
        try:
            r_tc = await client.get(
                "https://api.bcra.gob.ar/estadisticas/v3.0/monetarias",
                params={"idVariable": 4, "desde": "2023-01-01"},
            )
            tc_data = r_tc.json() if r_tc.status_code == 200 else []
        except Exception:
            tc_data = []
    return {"ipc": ipc_data, "tipo_cambio": tc_data}


@app.get("/api/v1/macro/base-monetaria", tags=["Macro"])
async def base_monetaria(db: Session = Depends(get_db)):
    """Base Monetaria en vivo desde BCRA con conversión a USD."""
    tc_usd = _get_tc_usd(db)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # idVariable 15 = Base Monetaria (millones de pesos)
            r = await client.get(
                "https://api.bcra.gob.ar/estadisticas/v3.0/monetarias",
                params={"idVariable": 15, "limit": 24},
            )
            data = r.json() if r.status_code == 200 else {}
            resultados = data.get("results", [])
            if not resultados:
                raise ValueError("Sin datos BCRA")

            # Último dato
            ultimo   = resultados[-1]
            bm_actual = float(ultimo.get("valor", 0)) * 1e6   # viene en millones

            # Dato diciembre 2023 (asunción)
            inicio_dato = next(
                (x for x in resultados if str(x.get("fecha", "")).startswith("2023-12")),
                resultados[0]
            )
            bm_inicio = float(inicio_dato.get("valor", 0)) * 1e6

            var_pct     = (bm_actual / bm_inicio - 1) * 100 if bm_inicio else 0
            multiplicador = bm_actual / bm_inicio if bm_inicio else 1

            serie_mensual = []
            for item in resultados:
                bm = float(item.get("valor", 0)) * 1e6
                bm_bill = bm / 1e12
                var = (bm / bm_inicio - 1) * 100 if bm_inicio else 0
                mult = bm / bm_inicio if bm_inicio else 1
                fecha_str = str(item.get("fecha", ""))
                label = fecha_str[:7] if fecha_str else "—"
                serie_mensual.append({
                    "label":    label,
                    "bm_bill":  round(bm_bill, 2),
                    "var_pct":  round(var, 1),
                    "mult":     round(mult, 2),
                })

            return {
                "inicio": {
                    "label":       str(inicio_dato.get("fecha", ""))[:7],
                    "bm_billones": round(bm_inicio / 1e12, 2),
                },
                "actual": {
                    "label":       str(ultimo.get("fecha", ""))[:7],
                    "bm_billones": round(bm_actual / 1e12, 2),
                    "bm_usd_mm":   round(bm_actual / tc_usd / 1e6, 0) if tc_usd else None,
                },
                "variacion_pct":  round(var_pct, 1),
                "multiplicador":  round(multiplicador, 2),
                "nota": (
                    f"Base Monetaria creció {round(multiplicador, 2)}x desde dic-2023. "
                    f"Variación acumulada: +{round(var_pct, 1)}%."
                ),
                "serie_mensual": serie_mensual,
            }

        except Exception as e:
            return {
                "error": f"No se pudo obtener Base Monetaria desde BCRA: {e}",
                "tc_usd_fallback": TC_USD_FALLBACK,
            }


@app.get("/api/v1/normativa/", tags=["Normativa"])
async def listar_normativa(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Listado de normas JGM."""
    items = db.query(models.Norma).offset(skip).limit(limit).all()
    total = db.query(func.count(models.Norma.id)).scalar()
    return {"total": total, "items": items}


@app.get("/api/v1/normativa/{norma_id}", tags=["Normativa"])
async def detalle_normativa(norma_id: int, db: Session = Depends(get_db)):
    """Detalle de una norma JGM."""
    norma = db.query(models.Norma).filter(models.Norma.id == norma_id).first()
    if not norma:
        raise HTTPException(status_code=404, detail="Norma no encontrada")
    return norma


@app.get("/api/v1/normativa/{norma_id}/partidas", tags=["Normativa"])
async def partidas_por_norma(norma_id: int, db: Session = Depends(get_db)):
    """Partidas afectadas por una norma JGM."""
    norma = db.query(models.Norma).filter(models.Norma.id == norma_id).first()
    if not norma:
        raise HTTPException(status_code=404, detail="Norma no encontrada")
    return {"norma_id": norma_id, "partidas": norma.partidas}


@app.get("/api/v1/comparativa/", tags=["Comparativa"])
async def comparativa(db: Session = Depends(get_db)):
    """Gasto nominal vs real vs inflación acumulada vs USD."""
    analizador = AnalizadorPresupuestario(db)
    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)
    return analizador.comparativa_total(ipc_factor=ipc_factor, tc_usd=tc_usd)


@app.post("/api/v1/scrape/trigger", tags=["Scraping"])
async def trigger_scrape(background_tasks: BackgroundTasks):
    """Dispara el scraper BORA en segundo plano."""
    from scripts.scraper_bora import scrape_bora
    background_tasks.add_task(scrape_bora)
    return {"status": "scraping iniciado", "timestamp": datetime.utcnow().isoformat()}


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "version": "2.2.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)