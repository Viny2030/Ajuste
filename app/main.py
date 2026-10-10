# app/main.py
"""
FastAPI - Monitor de Ajuste Presupuestario (MAP) v2.3.1
Fixes v2.3.0:
  - por-inciso: HAVING corregido para Postgres (no acepta alias del SELECT)
  - /api/v1/analisis/inciso: nuevo endpoint alias de por-inciso
  - /api/v1/partidas/: corregido para usar presupuesto_base en lugar de modelo Partida
  - sector: tolera 2026 sin datos (muestra 0 en lugar de null)
Fixes v2.3.1:
  - Los endpoints que solo hacen consultas SQLAlchemy sincronicas (ranking,
    por-inciso, inciso, sector, evolucion-real, partidas, normativa,
    comparativa, status) pasan de "async def" a "def". FastAPI los corre
    entonces en el threadpool en lugar de en el event loop: antes, al ser
    "async def" con db.execute() bloqueante adentro, cada request bloqueaba
    el event loop entero y las 3 llamadas concurrentes que dispara el
    dashboard (status + ranking + por-inciso) se serializaban en vez de
    correr en paralelo. Eso es lo que explica los 500 / timeouts
    intermitentes bajo carga concurrente (ej. mientras corre el sync diario).
  - base-monetaria (que sí necesita seguir siendo async por httpx.AsyncClient)
    ahora corre su unica consulta sincrona (_get_tc_usd) via run_in_threadpool
    en lugar de bloquear el loop directamente.
"""
import uvicorn
import httpx
import logging
from datetime import datetime, date
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from typing import List, Optional, Dict

from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, text

from app.database import models, schemas
from app.database.session import SessionLocal, engine
from app.core.engine import AnalizadorPresupuestario, cargar_macro_indices
from app.core.viz import generar_grafico_ajuste
from app.core import deflactor

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Monitor de Ajuste Presupuestario (MAP)",
    description="Analisis del ajuste presupuestario 2023-2026.",
    version="2.3.1",
)

# Archivos estaticos
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Router social — DESPUÉS de que app esté definido
from scripts.social.router_social import router as social_router
app.include_router(social_router)


# DB dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# MAPEO SECTORIAL DEFINITIVO - verificado contra sql_app.db mayo 2026
SECTORES: Dict[str, dict] = {
    "obra_publica": {
        "label": "Obra Publica / Infraestructura",
        "icon": "🏗️",
        "color": "#1a3a6e",
        "jur_2023": [64, 57, 65],
        "prg_2023": None,
        "jur_2026": [50],
        "prg_2026": [62, 63, 48, 51, 82, 54, 16, 37, 69, 57, 5, 15, 52],
    },
    "jubilaciones": {
        "label": "Jubilaciones y Pensiones",
        "icon": "👴",
        "color": "#3d1e6e",
        "jur_2023": [75],
        "prg_2023": [16, 17, 21, 30, 31],
        "jur_2026": [88],
        "prg_2026": [16, 17, 21, 30, 31],
    },
    "jubilaciones_fuerzas": {
        "label": "Jubilaciones Fuerzas Armadas y Seguridad",
        "icon": "🛡️",
        "color": "#6b7280",
        "jur_2023": [41, 45],
        "prg_2023": [16, 18, 19, 20, 21, 22],
        "jur_2026": [41, 45],
        "prg_2026": [16, 18, 19, 20, 21, 22],
    },
    "capital_humano": {
        "label": "Capital Humano (Educ + Ninez + Empleo)",
        "icon": "📚",
        "color": "#7a4500",
        "jur_2023": [70, 75, 85],
        "prg_2023": None,
        "prg_excluir_2023": {75: [16, 17, 21, 30, 31]},
        "jur_2026": [88],
        "prg_2026": None,
        "prg_excluir_2026": {88: [16, 17, 21, 30, 31]},
    },
    "salud": {
        "label": "Salud",
        "icon": "🏥",
        "color": "#145a2a",
        "jur_2023": [80],
        "prg_2023": None,
        "prg_excluir_2023": {},
        "jur_2026": [80],
        "prg_2026": None,
        "prg_excluir_2026": {80: [23, 36, 69, 70]},
    },
    "seguridad": {
        "label": "Seguridad (Fuerzas Federales)",
        "icon": "🚔",
        "color": "#dc2626",
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

# CONSTANTES MACRO
IPC_FACTOR_ACUMULADO_FALLBACK = 10.53
TC_USD_FALLBACK               = 1395

IPC_POR_ANIO_FALLBACK = {
    2023: 1.0,
    2024: 3.2,
    2025: 4.21,
    2026: 4.21
}


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _sumar_presupuesto(
    db: Session,
    jurisdicciones: List[int],
    ejercicio: int,
    programas: Optional[List[int]] = None,
    prg_excluir: Optional[Dict[int, List[int]]] = None,
) -> float:
    # Se compara crédito VIGENTE contra crédito VIGENTE (antes: original 2023
    # contra vigente 2026, que mezcla dos conceptos distintos).
    campo = "monto_vigente"
    jur_in = ", ".join(f"'{j}'" for j in jurisdicciones)

    prg_clause = ""
    if programas:
        prg_in = ", ".join(f"'{p}'" for p in programas)
        prg_clause = f" AND programa_id IN ({prg_in})"

    excl_clauses = []
    if prg_excluir:
        for jur_id, prgs in prg_excluir.items():
            if prgs and jur_id in jurisdicciones:
                prg_excl_in = ", ".join(f"'{p}'" for p in prgs)
                excl_clauses.append(
                    f"NOT (jurisdiccion_id = '{jur_id}' AND programa_id IN ({prg_excl_in}))"
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


def _get_ipc_factor(db: Session = None, anio_base: int = 2023, anio_comp: int = 2026) -> float:
    """Factor para pasar pesos de anio_comp a pesos de anio_base.

    Antes filtraba MacroIndice.tipo (campo inexistente: se llama "indicador"),
    fallaba en silencio y devolvía siempre la constante 10,53 (inflación punta
    a punta dic-2022 → may-2026). Ahora usa app/core/deflactor.py: relación de
    precios promedio anuales, con datos del BCRA (o el CSV de respaldo).
    """
    return deflactor.factor(anio_base, anio_comp)


def _get_tc_usd(db: Session = None) -> float:
    """Tipo de cambio oficial actual (para valuar stocks del día, p. ej. base monetaria)."""
    return deflactor.tc_actual() or TC_USD_FALLBACK


def _tc_promedio(anio: int) -> float:
    """Tipo de cambio promedio del año, para pasar créditos anuales a dólares."""
    return deflactor.tc_promedio(anio)


# ── ROOT ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Home"], include_in_schema=False)
async def root():
    for nombre in ("main.html", "index.html"):
        path = os.path.join(static_dir, nombre)
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse("<h1>Dashboard no encontrado.</h1>")


@app.get("/api/v1/status", tags=["Health"])
def status(db: Session = Depends(get_db)):
    ipc = _get_ipc_factor(db)
    return {
        "app": "Monitor de Ajuste Presupuestario",
        "version": "2.3.1",
        "factor_ipc_acumulado": round(ipc, 4),
        "deflactor": deflactor.metadata(),
        "servidor_tiempo": datetime.utcnow().isoformat(),
    }


@app.get("/dashboard", tags=["Home"], include_in_schema=False)
async def dashboard():
    for nombre in ("main.html", "index.html"):
        path = os.path.join(static_dir, nombre)
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse("<h1>Dashboard no encontrado.</h1>")


@app.get("/manual", tags=["Home"], include_in_schema=False)
async def manual():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="https://github.com/Viny2030/Ajuste#readme")


# ── RANKING ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/ranking", tags=["Analisis"])
def ranking_ajuste(
    top_n: int = Query(20, ge=1, le=500, alias="top_n"),
    top: int = Query(20, ge=1, le=500),
    anio_base: int = Query(2023),
    anio_comp: int = Query(2026),
    db: Session = Depends(get_db),
):
    n = top_n if top_n != 20 else top
    ipc_factor = _get_ipc_factor(db, anio_base, anio_comp)
    tc_base    = _tc_promedio(anio_base)
    tc_comp    = _tc_promedio(anio_comp)

    # Se agrega cada año por separado y recién después se cruza. Antes se hacía
    # un LEFT JOIN partida-a-partida y luego SUM, con lo que cada suma quedaba
    # multiplicada por la cantidad de filas del otro año (cociente distorsionado).
    sql = text("""
        WITH b AS (
            SELECT jurisdiccion_id, programa_id, inciso_id,
                   MAX(jurisdiccion_desc) AS jurisdiccion_desc,
                   MAX(programa_desc)     AS programa_desc,
                   MAX(inciso_desc)       AS inciso_desc,
                   COALESCE(SUM(monto_vigente), 0)  AS base_vigente,
                   COALESCE(SUM(monto_original), 0) AS base_original
            FROM presupuesto_base
            WHERE ejercicio = :anio_base
            GROUP BY jurisdiccion_id, programa_id, inciso_id
        ),
        c AS (
            SELECT jurisdiccion_id, programa_id, inciso_id,
                   COALESCE(SUM(monto_vigente), 0) AS comp_vigente
            FROM presupuesto_base
            WHERE ejercicio = :anio_comp
            GROUP BY jurisdiccion_id, programa_id, inciso_id
        )
        SELECT b.*, c.comp_vigente
        FROM b
        JOIN c ON  c.jurisdiccion_id = b.jurisdiccion_id
               AND c.programa_id     = b.programa_id
               AND c.inciso_id       = b.inciso_id
        WHERE b.base_vigente > 0 AND c.comp_vigente > 0
        ORDER BY (c.comp_vigente / b.base_vigente) ASC
        LIMIT :top_n
    """)

    rows = db.execute(sql, {
        "anio_base": anio_base,
        "anio_comp": anio_comp,
        "top_n":     n,
    }).fetchall()

    resultado = []
    for r in rows:
        base     = float(r.base_vigente) or 1
        vig      = float(r.comp_vigente)
        var_nom  = (vig / base - 1) * 100
        var_real = (vig / ipc_factor / base - 1) * 100
        # Descomposición aditiva: real = recorte nominal + licuación (ver /analisis/licuacion)
        lic      = var_real - min(var_nom, 0.0)
        var_usd  = (
            (vig / tc_comp) / (base / tc_base) - 1
        ) * 100 if tc_comp and tc_base else None

        resultado.append({
            "jurisdiccion_id":       r.jurisdiccion_id,
            "jurisdiccion":          r.jurisdiccion_desc,
            "programa_id":           r.programa_id,
            "programa_desc":         r.programa_desc,
            "inciso_id":             r.inciso_id,
            "monto_base":            round(base, 0),          # crédito vigente año base
            "monto_original":        round(float(r.base_original), 0),  # referencia: crédito inicial ley
            "monto_vigente":         round(vig,  0),
            "variacion_nominal_pct": round(var_nom,  1),
            "variacion_real_pct":    round(var_real, 1),
            "licuacion_pct":         round(lic,      1),
            "ajuste_usd_pct":        round(var_usd,  1) if var_usd is not None else None,
            "ajuste_nominal_abs":    round(vig - base, 0),
            "ajuste_real_abs":       round(vig / ipc_factor - base, 0),
            "ajuste_usd_abs":        round(vig / tc_comp - base / tc_base, 0) if tc_comp and tc_base else None,
            # Con tilde: es lo que compara la página (antes "REDUCCION" hacía que
            # el filtro "solo reducciones" no devolviera nada)
            "estado_ajuste":         "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
        })

    return {
        "advertencia": (
            "Cruce por programa_id+inciso_id. "
            "Usar /api/v1/analisis/sector para sectores correctos."
        ),
        "ipc_factor": round(ipc_factor, 4),
        "deflactor":  deflactor.metadata(anio_base, anio_comp),
        "ranking":    resultado,
    }


# ── POR INCISO ────────────────────────────────────────────────────────────────

def _calcular_por_inciso(anio: int, db: Session) -> list:
    ipc_factor = _get_ipc_factor(db, 2023, anio)

    sql = text("""
        SELECT
            inciso_id,
            MAX(inciso_desc) AS inciso_desc,
            SUM(CASE WHEN ejercicio = 2023  THEN monto_vigente  ELSE 0 END) AS total_base,
            SUM(CASE WHEN ejercicio = 2023  THEN monto_original ELSE 0 END) AS total_original,
            SUM(CASE WHEN ejercicio = :anio THEN monto_vigente  ELSE 0 END) AS total_vigente
        FROM presupuesto_base
        WHERE ejercicio IN (2023, :anio)
        GROUP BY inciso_id
        HAVING SUM(CASE WHEN ejercicio = 2023 THEN monto_vigente ELSE 0 END) > 0
        ORDER BY inciso_id
    """)

    rows = db.execute(sql, {"anio": anio}).fetchall()

    resultado = []
    for r in rows:
        base     = float(r.total_base) or 1
        vig      = float(r.total_vigente)
        var_nom  = (vig / base - 1) * 100
        var_real = (vig / ipc_factor / base - 1) * 100

        resultado.append({
            "inciso_id":              r.inciso_id,
            "inciso_desc":            r.inciso_desc,
            "total_base_2023":        round(base, 0),   # crédito vigente 2023
            "total_original":         round(float(r.total_original), 0),  # referencia
            "total_vigente":          round(vig,  0),
            "total_real_moneda_2023": round(vig / ipc_factor, 0),
            "variacion_nominal_pct":  round(var_nom,  1),
            "variacion_real_pct":     round(var_real, 1),
        })

    return resultado


@app.get("/api/v1/analisis/por-inciso", tags=["Analisis"])
def analisis_por_inciso(
    anio: int = Query(2026),
    db: Session = Depends(get_db),
):
    return _calcular_por_inciso(anio, db)


@app.get("/api/v1/analisis/inciso", tags=["Analisis"])
def analisis_inciso(
    inciso_id: Optional[str] = Query(None),
    anio: int = Query(2026),
    db: Session = Depends(get_db),
):
    todos = _calcular_por_inciso(anio, db)
    if inciso_id is not None:
        filtrado = [x for x in todos if str(x["inciso_id"]) == str(inciso_id)]
        if not filtrado:
            raise HTTPException(
                status_code=404,
                detail=f"inciso_id='{inciso_id}' no encontrado."
            )
        return filtrado
    return todos


# ── SECTOR ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/sector", tags=["Analisis"])
def analisis_sector(
    sector: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if sector and sector not in SECTORES:
        raise HTTPException(
            status_code=404,
            detail=f"Sector '{sector}' no reconocido. Opciones: {list(SECTORES.keys())}",
        )

    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _tc_promedio(2026)
    tc_2023    = _tc_promedio(2023)
    sectores_a_calcular = {sector: SECTORES[sector]} if sector else SECTORES

    hay_2026 = db.execute(
        text("SELECT COUNT(*) FROM presupuesto_base WHERE ejercicio = 2026")
    ).scalar() or 0

    resultados = []
    for clave, cfg in sectores_a_calcular.items():
        monto_2023 = _sumar_presupuesto(
            db, cfg["jur_2023"], 2023,
            cfg.get("prg_2023"), cfg.get("prg_excluir_2023"),
        )
        monto_2026 = _sumar_presupuesto(
            db, cfg["jur_2026"], 2026,
            cfg.get("prg_2026"), cfg.get("prg_excluir_2026"),
        ) if hay_2026 else None

        if monto_2023 > 0 and monto_2026 is not None and monto_2026 > 0:
            var_nominal  = (monto_2026 / monto_2023 - 1) * 100
            var_real_ipc = (monto_2026 / ipc_factor / monto_2023 - 1) * 100
        else:
            var_nominal = var_real_ipc = None

        if monto_2023 > 0 and monto_2026 and tc_2023 > 0 and tc_usd > 0:
            monto_2023_usd = monto_2023 / tc_2023
            monto_2026_usd = monto_2026 / tc_usd
            var_real_usd   = (monto_2026_usd / monto_2023_usd - 1) * 100
        else:
            monto_2023_usd = monto_2026_usd = var_real_usd = None

        resultados.append({
            "sector":                   clave,
            "label":                    cfg["label"],
            "icon":                     cfg["icon"],
            "color":                    cfg["color"],
            "jur_2023":                 cfg["jur_2023"],
            "jur_2026":                 cfg["jur_2026"],
            "prg_2023":                 cfg.get("prg_2023"),
            "prg_2026":                 cfg.get("prg_2026"),
            "credito_vigente_2023_mm":  round(monto_2023 / 1e6, 1),
            "credito_vigente_2026_mm":  round(monto_2026 / 1e6, 1) if monto_2026 else None,
            "var_nominal_pct":          round(var_nominal,  1) if var_nominal  is not None else None,
            "var_real_ipc_pct":         round(var_real_ipc, 1) if var_real_ipc is not None else None,
            "var_real_usd_pct":         round(var_real_usd, 1) if var_real_usd is not None else None,
            "credito_2023_usd_mm":      round(monto_2023_usd / 1e6, 1) if monto_2023_usd else None,
            "credito_2026_usd_mm":      round(monto_2026_usd / 1e6, 1) if monto_2026_usd else None,
            "ipc_factor":               round(ipc_factor, 4),
            "tc_usd":                   round(tc_usd, 2),
            "advertencia_2026":         None if hay_2026 else "Sin datos 2026. Correr seed_2026.py",
        })

    return {
        "generado_en":          datetime.utcnow().isoformat(),
        "ipc_factor_acumulado": round(ipc_factor, 4),
        "tc_usd_vigente":       round(tc_usd, 2),   # promedio 2026
        "tc_usd_inicio_2023":   round(tc_2023, 2),  # promedio 2023
        "deflactor":            deflactor.metadata(),
        "hay_datos_2026":       bool(hay_2026),
        "advertencia_mapeo": (
            "Obra publica 2026 en jur 50 prg especificos. "
            "Jubilaciones: jur 75->88. Capital Humano: jur 70+75+85->88."
        ),
        "sectores": resultados,
    }


# ── EVOLUCION REAL ────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/evolucion-real", tags=["Analisis"])
def evolucion_real(
    jurisdiccion_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    jur_clause = "AND jurisdiccion_id = :jur" if jurisdiccion_id else ""
    sql = text(f"""
        SELECT ejercicio,
               SUM(monto_original) AS total_original,
               SUM(monto_vigente)  AS total_vigente
        FROM presupuesto_base
        WHERE 1=1 {jur_clause}
        GROUP BY ejercicio
        ORDER BY ejercicio
    """)
    params = {"jur": str(jurisdiccion_id)} if jurisdiccion_id else {}
    rows = db.execute(sql, params).fetchall()

    # Mismo criterio para todos los años (antes: 3,2 / 4,21 / 10,53 mezclaba
    # promedios anuales con inflación punta a punta y fabricaba un -55 % en 2026).
    resultado = []
    prev_real = None
    for r in rows:
        anio     = r.ejercicio
        nom      = float(r.total_vigente or r.total_original or 0)
        ipc_anio = _get_ipc_factor(db, 2023, anio)
        real     = nom / ipc_anio if ipc_anio else nom
        var_yoy  = (real / prev_real - 1) * 100 if prev_real else None
        prev_real = real

        resultado.append({
            "ejercicio":              anio,
            "total_nominal":          round(nom,  0),
            "total_real":             round(real, 0),
            "factor_ipc":             round(ipc_anio, 2),
            "variacion_real_pct_yoy": round(var_yoy, 1) if var_yoy is not None else None,
        })

    return resultado


# ── LICUACIÓN VS RECORTE ──────────────────────────────────────────────────────

@app.get("/api/v1/analisis/licuacion", tags=["Analisis"])
def licuacion(
    anio_base: int = Query(2023),
    anio_comp: int = Query(2026),
    db: Session = Depends(get_db),
):
    """Descomposición de la variación real por jurisdicción (todas las partidas,
    ponderado por monto).

        variación real (%) = recorte nominal (pp) + licuación (pp)
        recorte nominal = variación nominal si es negativa (si no, 0)
        licuación       = el resto: pérdida por no actualizar el crédito al
                          ritmo de la inflación (negativa) o ganancia real (positiva)

    Antes la página promediaba sin ponderar sólo los 500 programas con mayor
    caída y llamaba "licuación" a la resta nominal − real.
    Sólo se comparan jurisdicciones con el mismo código en ambos años; las
    reorganizadas (p. ej. 57, 64, 65, 70, 75, 85 → 50/88) se listan aparte.
    """
    ipc = _get_ipc_factor(db, anio_base, anio_comp)
    rows = db.execute(text("""
        SELECT jurisdiccion_id, MAX(jurisdiccion_desc) AS jurisdiccion_desc,
               SUM(CASE WHEN ejercicio = :b THEN monto_vigente ELSE 0 END) AS base,
               SUM(CASE WHEN ejercicio = :c THEN monto_vigente ELSE 0 END) AS comp
        FROM presupuesto_base
        WHERE ejercicio IN (:b, :c)
        GROUP BY jurisdiccion_id
    """), {"b": anio_base, "c": anio_comp}).fetchall()

    def fila(nombre, base, comp, jid=None):
        nom = (comp / base - 1) * 100
        real = (comp / ipc / base - 1) * 100
        recorte = min(nom, 0.0)
        return {
            "jurisdiccion_id": jid, "jurisdiccion": nombre,
            "base": round(base, 0), "vigente": round(comp, 0),
            "variacion_nominal_pct": round(nom, 1),
            "variacion_real_pct": round(real, 1),
            "recorte_nominal_pp": round(recorte, 1),
            "licuacion_pp": round(real - recorte, 1),
        }

    comparables, sin_equivalente = [], []
    tb = tc = 0.0
    for r in rows:
        b, c = float(r.base or 0), float(r.comp or 0)
        if b > 0 and c > 0:
            comparables.append(fila(r.jurisdiccion_desc, b, c, r.jurisdiccion_id))
            tb += b; tc += c
        elif b > 0 or c > 0:
            sin_equivalente.append({"jurisdiccion_id": r.jurisdiccion_id,
                                    "jurisdiccion": r.jurisdiccion_desc,
                                    "solo_en": anio_base if b > 0 else anio_comp})
    comparables.sort(key=lambda x: x["variacion_real_pct"])
    return {
        "deflactor": deflactor.metadata(anio_base, anio_comp),
        "total_comparable": fila("Total jurisdicciones comparables", tb, tc) if tb else None,
        "jurisdicciones": comparables,
        "sin_equivalente_directo": sin_equivalente,
    }


# ── PARTIDAS ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/partidas/", tags=["Partidas"])
def listar_partidas(
    jurisdiccion_id: Optional[str] = None,
    ejercicio: Optional[int] = None,
    inciso_id: Optional[str] = None,
    skip: int = 0,
    limit: int = Query(100, le=1000),
    db: Session = Depends(get_db),
):
    conditions = []
    params: dict = {"skip": skip, "limit": limit}

    if jurisdiccion_id:
        conditions.append("jurisdiccion_id = :jur_id")
        params["jur_id"] = str(jurisdiccion_id)
    if ejercicio:
        conditions.append("ejercicio = :ejercicio")
        params["ejercicio"] = ejercicio
    if inciso_id:
        conditions.append("inciso_id = :inciso_id")
        params["inciso_id"] = str(inciso_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = db.execute(
        text(f"SELECT COUNT(*) FROM presupuesto_base {where}"),
        {k: v for k, v in params.items() if k not in ("skip", "limit")}
    ).scalar()
    rows = db.execute(
        text(f"SELECT * FROM presupuesto_base {where} ORDER BY id OFFSET :skip LIMIT :limit"),
        params
    ).fetchall()

    return {"total": total, "skip": skip, "limit": limit, "items": [dict(r._mapping) for r in rows]}


# ── MACRO ─────────────────────────────────────────────────────────────────────

BCRA_V4_BASE = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"


async def _fetch_bcra_v4_async(client: httpx.AsyncClient, id_variable: int, desde: str = "2023-01-01") -> list[dict]:
    """
    Consulta una serie BCRA v4.0 y devuelve su 'detalle' (lista de
    {"fecha","valor"}) ordenado ascendente, o [] si falla.

    Nota (2026-07): v3.0 y v2.0 fueron deprecadas por el BCRA (410 Gone).
    v4.0 además anida la respuesta distinto: los datos están en
    results[0]["detalle"], no directamente en "results" como antes.
    """
    try:
        r = await client.get(f"{BCRA_V4_BASE}/{id_variable}", params={"desde": desde})
        if r.status_code != 200:
            return []
        data = r.json()
        resultados = data.get("results", [])
        if not resultados:
            return []
        detalle = resultados[0].get("detalle", [])
        return sorted(detalle, key=lambda x: x["fecha"])
    except Exception:
        return []


@app.get("/api/v1/macro/series", tags=["Macro"])
async def macro_series():
    async with httpx.AsyncClient(timeout=15) as client:
        ipc_data = await _fetch_bcra_v4_async(client, 27)   # Inflación mensual (%)
        tc_data = await _fetch_bcra_v4_async(client, 4)     # TC minorista (promedio vendedor)
    return {"ipc": ipc_data, "tipo_cambio": tc_data}


@app.get("/api/v1/macro/base-monetaria", tags=["Macro"])
async def base_monetaria(db: Session = Depends(get_db)):
    tc_usd = await run_in_threadpool(_get_tc_usd, db)
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            # id 15 = Base monetaria, periodicidad DIARIA — pedir por rango de
            # fecha (no "limit=24", que traería solo los últimos 24 días, no
            # los últimos 24 meses) y resamplear a mensual acá.
            detalle = await _fetch_bcra_v4_async(client, 15, desde="2023-12-01")
            if not detalle:
                raise ValueError("Sin datos BCRA")

            import pandas as pd
            df = pd.DataFrame(detalle)
            df["fecha"] = pd.to_datetime(df["fecha"])
            df_mensual = (
                df.set_index("fecha")["valor"]
                .resample("ME").last()
                .dropna()
                .reset_index()
            )
            if df_mensual.empty:
                raise ValueError("Sin datos BCRA tras resamplear")

            ultimo = df_mensual.iloc[-1]
            bm_actual = float(ultimo["valor"]) * 1e6

            inicio_rows = df_mensual[df_mensual["fecha"].dt.strftime("%Y-%m") == "2023-12"]
            inicio_row = inicio_rows.iloc[0] if not inicio_rows.empty else df_mensual.iloc[0]
            bm_inicio = float(inicio_row["valor"]) * 1e6

            var_pct = (bm_actual / bm_inicio - 1) * 100 if bm_inicio else 0
            multiplicador = bm_actual / bm_inicio if bm_inicio else 1

            serie_mensual = []
            for _, row in df_mensual.iterrows():
                bm = float(row["valor"]) * 1e6
                mult = bm / bm_inicio if bm_inicio else 1
                serie_mensual.append({
                    "label": row["fecha"].strftime("%Y-%m"),
                    "bm_bill": round(bm / 1e12, 2),
                    "var_pct": round((bm / bm_inicio - 1) * 100, 1) if bm_inicio else 0,
                    "mult": round(mult, 2),
                })

            return {
                "inicio": {"label": inicio_row["fecha"].strftime("%Y-%m"), "bm_billones": round(bm_inicio / 1e12, 2)},
                "actual": {"label": ultimo["fecha"].strftime("%Y-%m"), "bm_billones": round(bm_actual / 1e12, 2), "bm_usd_mm": round(bm_actual / tc_usd / 1e6, 0) if tc_usd else None},
                "variacion_pct": round(var_pct, 1),
                "multiplicador": round(multiplicador, 2),
                "serie_mensual": serie_mensual,
            }
        except Exception as e:
            return {"error": f"No se pudo obtener Base Monetaria: {e}"}


# ── NORMATIVA ─────────────────────────────────────────────────────────────────
# Antes estos endpoints consultaban models.Norma (no existe: el modelo es
# NormaJGM, y la tabla normas_jgm está vacía) y /comparativa llamaba a
# AnalizadorPresupuestario.comparativa_total(), que no existe: los cuatro
# daban error 500. Ahora /normativa/ sirve las normas presupuestarias que
# descubre el workflow diario (data/nuevas_das.json) y el histórico procesado
# (data/processed/das_presupuesto_2023_2026.json). /comparativa se quitó:
# lo cubre /api/v1/analisis/sector.
import json as _json
from pathlib import Path as _Path

_DATA = _Path(__file__).resolve().parents[1] / "data"


def _leer_lista(ruta: _Path) -> list:
    try:
        d = _json.loads(ruta.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


@app.get("/api/v1/normativa/", tags=["Normativa"])
def listar_normativa(skip: int = 0, limit: int = Query(50, le=500)):
    recientes = _leer_lista(_DATA / "nuevas_das.json")
    historico = _leer_lista(_DATA / "processed" / "das_presupuesto_2023_2026.json")
    items = sorted(recientes + historico,
                   key=lambda x: str(x.get("fecha_boletin", "")), reverse=True)
    return {"total": len(items), "recientes_desde_bora": len(recientes),
            "items": items[skip: skip + limit]}


# ── SCRAPING / HEALTH ─────────────────────────────────────────────────────────

# Se quitó POST /api/v1/scrape/trigger: era público (sin autenticación) e
# importaba scripts.scraper_bora, que no existe. El descubrimiento de normas
# corre en el workflow "Daily BORA Discovery".


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "version": "2.3.1", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
