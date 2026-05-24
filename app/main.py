# app/main.py
"""
FastAPI - Monitor de Ajuste Presupuestario (MAP) v2.3.0
Fixes v2.3.0:
  - por-inciso: HAVING corregido para Postgres (no acepta alias del SELECT)
  - /api/v1/analisis/inciso: nuevo endpoint alias de por-inciso
  - /api/v1/partidas/: corregido para usar presupuesto_base en lugar de modelo Partida
  - sector: tolera 2026 sin datos (muestra 0 en lugar de null)
"""
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
from scripts.social.router_social import router as social_router
app.include_router(social_router)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Monitor de Ajuste Presupuestario (MAP)",
    description="Analisis del ajuste presupuestario 2023-2026.",
    version="2.3.0",
)

# Archivos estaticos
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


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
TC_USD_INICIO_2023            = 187.0
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
    campo = "monto_original" if ejercicio == 2023 else "monto_vigente"
    jur_in = ", ".join(f"'{j}'" for j in jurisdicciones)  # VARCHAR en DB

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


def _get_ipc_factor(db: Session) -> float:
    try:
        filas = (
            db.query(models.MacroIndice)
            .filter(and_(
                models.MacroIndice.tipo == "IPC_variacion_mensual",
                models.MacroIndice.fecha >= date(2023, 1, 1),
            ))
            .order_by(models.MacroIndice.fecha)
            .all()
        )
        if not filas:
            return IPC_FACTOR_ACUMULADO_FALLBACK
        factor = 1.0
        for f in filas:
            factor *= 1 + (float(f.valor) / 100)
        return factor
    except Exception:
        return IPC_FACTOR_ACUMULADO_FALLBACK


def _get_tc_usd(db: Session) -> float:
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


# ── ROOT ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Home"], include_in_schema=False)
async def root(db: Session = Depends(get_db)):
    ipc = _get_ipc_factor(db)
    return {
        "app": "Monitor de Ajuste Presupuestario",
        "version": "2.3.0",
        "factor_ipc_acumulado": round(ipc, 4),
        "servidor_tiempo": datetime.utcnow().isoformat(),
    }


@app.get("/dashboard", tags=["Home"], include_in_schema=False)
async def dashboard():
    for nombre in ("main.html", "index.html"):
        path = os.path.join(static_dir, nombre)
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse("<h1>Dashboard no encontrado.</h1>")


# ── RANKING ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/ranking", tags=["Analisis"])
async def ranking_ajuste(
    top_n: int = Query(20, ge=1, le=500, alias="top_n"),
    top: int = Query(20, ge=1, le=500),
    anio_base: int = Query(2023),
    anio_comp: int = Query(2026),
    db: Session = Depends(get_db),
):
    n = top_n if top_n != 20 else top
    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)

    sql = text("""
        SELECT
            b.jurisdiccion_id,
            b.jurisdiccion_desc,
            b.programa_id,
            b.programa_desc,
            b.inciso_id,
            b.inciso_desc,
            COALESCE(SUM(b.monto_original), 0) AS monto_original,
            COALESCE(SUM(c.monto_vigente),  0) AS monto_vigente
        FROM presupuesto_base b
        LEFT JOIN presupuesto_base c
            ON  c.jurisdiccion_id = b.jurisdiccion_id
            AND c.programa_id     = b.programa_id
            AND c.inciso_id       = b.inciso_id
            AND c.ejercicio       = :anio_comp
        WHERE b.ejercicio = :anio_base
          AND b.monto_original > 0
        GROUP BY
            b.jurisdiccion_id, b.jurisdiccion_desc,
            b.programa_id,     b.programa_desc,
            b.inciso_id,       b.inciso_desc
        HAVING COALESCE(SUM(c.monto_vigente), 0) > 0
        ORDER BY
            (COALESCE(SUM(c.monto_vigente), 0) / :ipc / COALESCE(SUM(b.monto_original), 1)) ASC
        LIMIT :top_n
    """)

    rows = db.execute(sql, {
        "anio_base": anio_base,
        "anio_comp": anio_comp,
        "ipc":       ipc_factor,
        "top_n":     n,
    }).fetchall()

    resultado = []
    for r in rows:
        orig     = float(r.monto_original) or 1
        vig      = float(r.monto_vigente)
        var_nom  = (vig / orig - 1) * 100
        var_real = (vig / ipc_factor / orig - 1) * 100
        lic      = var_nom - var_real
        var_usd  = (
            (vig / tc_usd) / (orig / TC_USD_INICIO_2023) - 1
        ) * 100 if tc_usd and TC_USD_INICIO_2023 else None

        resultado.append({
            "jurisdiccion_id":       r.jurisdiccion_id,
            "jurisdiccion":          r.jurisdiccion_desc,
            "programa_id":           r.programa_id,
            "programa_desc":         r.programa_desc,
            "inciso_id":             r.inciso_id,
            "monto_original":        round(orig, 0),
            "monto_vigente":         round(vig,  0),
            "variacion_nominal_pct": round(var_nom,  1),
            "variacion_real_pct":    round(var_real, 1),
            "licuacion_pct":         round(lic,      1),
            "ajuste_usd_pct":        round(var_usd,  1) if var_usd is not None else None,
            "estado_ajuste":         "REDUCCION" if var_real < 0 else "INCREMENTO",
        })

    return {
        "advertencia": (
            "Cruce por programa_id+inciso_id. "
            "Usar /api/v1/analisis/sector para sectores correctos."
        ),
        "ipc_factor": round(ipc_factor, 4),
        "ranking":    resultado,
    }


# ── POR INCISO (fix HAVING para Postgres) ─────────────────────────────────────

def _calcular_por_inciso(anio: int, db: Session) -> list:
    ipc_factor = _get_ipc_factor(db)

    # FIX: Postgres no acepta aliases del SELECT en HAVING — repetir la expresión
    sql = text("""
        SELECT
            inciso_id,
            inciso_desc,
            SUM(CASE WHEN ejercicio = 2023  THEN monto_original ELSE 0 END) AS total_original,
            SUM(CASE WHEN ejercicio = :anio THEN monto_vigente  ELSE 0 END) AS total_vigente
        FROM presupuesto_base
        WHERE ejercicio IN (2023, :anio)
        GROUP BY inciso_id, inciso_desc
        HAVING SUM(CASE WHEN ejercicio = 2023 THEN monto_original ELSE 0 END) > 0
        ORDER BY inciso_id
    """)

    rows = db.execute(sql, {"anio": anio}).fetchall()

    resultado = []
    for r in rows:
        orig     = float(r.total_original) or 1
        vig      = float(r.total_vigente)
        var_nom  = (vig / orig - 1) * 100
        var_real = (vig / ipc_factor / orig - 1) * 100

        resultado.append({
            "inciso_id":              r.inciso_id,
            "inciso_desc":            r.inciso_desc,
            "total_original":         round(orig, 0),
            "total_vigente":          round(vig,  0),
            "total_real_moneda_2023": round(vig / ipc_factor, 0),
            "variacion_nominal_pct":  round(var_nom,  1),
            "variacion_real_pct":     round(var_real, 1),
        })

    return resultado


@app.get("/api/v1/analisis/por-inciso", tags=["Analisis"])
async def analisis_por_inciso(
    anio: int = Query(2026),
    db: Session = Depends(get_db),
):
    return _calcular_por_inciso(anio, db)


# NUEVO: alias con query param inciso_id para el frontend
@app.get("/api/v1/analisis/inciso", tags=["Analisis"])
async def analisis_inciso(
    inciso_id: Optional[str] = Query(None, description="ID del inciso (ej: 1). Si no se pasa, devuelve todos."),
    anio: int = Query(2026),
    db: Session = Depends(get_db),
):
    """
    Alias de /por-inciso con filtro opcional por inciso_id.
    Ejemplo: /api/v1/analisis/inciso?inciso_id=1
    """
    todos = _calcular_por_inciso(anio, db)
    if inciso_id is not None:
        filtrado = [x for x in todos if str(x["inciso_id"]) == str(inciso_id)]
        if not filtrado:
            raise HTTPException(
                status_code=404,
                detail=f"inciso_id='{inciso_id}' no encontrado. "
                       f"IDs disponibles: {[x['inciso_id'] for x in todos]}"
            )
        return filtrado
    return todos


# ── SECTOR ────────────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/sector", tags=["Analisis"])
async def analisis_sector(
    sector: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if sector and sector not in SECTORES:
        raise HTTPException(
            status_code=404,
            detail=f"Sector '{sector}' no reconocido. Opciones: {list(SECTORES.keys())}",
        )

    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)
    sectores_a_calcular = {sector: SECTORES[sector]} if sector else SECTORES

    # check si hay datos 2026
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

        if monto_2023 > 0 and monto_2026 and TC_USD_INICIO_2023 > 0 and tc_usd > 0:
            monto_2023_usd = monto_2023 / TC_USD_INICIO_2023
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
            "credito_original_2023_mm": round(monto_2023 / 1e6, 1),
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
        "tc_usd_vigente":       round(tc_usd, 2),
        "tc_usd_inicio_2023":   TC_USD_INICIO_2023,
        "hay_datos_2026":       bool(hay_2026),
        "advertencia_mapeo": (
            "Obra publica 2026 en jur 50 prg especificos. "
            "Jubilaciones: jur 75->88. Capital Humano: jur 70+75+85->88."
        ),
        "sectores": resultados,
    }


# ── EVOLUCION REAL ────────────────────────────────────────────────────────────

@app.get("/api/v1/analisis/evolucion-real", tags=["Analisis"])
async def evolucion_real(
    jurisdiccion_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    ipc_factor = _get_ipc_factor(db)

    jur_clause = "AND jurisdiccion_id = :jur" if jurisdiccion_id else ""
    sql = text(f"""
        SELECT
            ejercicio,
            SUM(monto_original) AS total_original,
            SUM(monto_vigente)  AS total_vigente
        FROM presupuesto_base
        WHERE 1=1 {jur_clause}
        GROUP BY ejercicio
        ORDER BY ejercicio
    """)
    params = {"jur": str(jurisdiccion_id)} if jurisdiccion_id else {}
    rows = db.execute(sql, params).fetchall()

    IPC_POR_ANIO = {2023: 1.0, 2024: 3.2, 2025: 4.21, 2026: ipc_factor}

    resultado = []
    for i, r in enumerate(rows):
        anio     = r.ejercicio
        nom      = float(r.total_vigente or r.total_original or 0)
        ipc_anio = IPC_POR_ANIO.get(anio, ipc_factor)
        real     = nom / ipc_anio

        if i == 0:
            var_yoy = None
        else:
            prev_nom  = float(rows[i-1].total_vigente or rows[i-1].total_original or 1)
            prev_real = prev_nom / IPC_POR_ANIO.get(rows[i-1].ejercicio, ipc_factor)
            var_yoy   = (real / prev_real - 1) * 100 if prev_real else None

        resultado.append({
            "ejercicio":              anio,
            "total_nominal":          round(nom,  0),
            "total_real":             round(real, 0),
            "factor_ipc":             round(ipc_anio, 2),
            "variacion_real_pct_yoy": round(var_yoy, 1) if var_yoy is not None else None,
        })

    return resultado


# ── PARTIDAS (fix: usa presupuesto_base directamente) ─────────────────────────

@app.get("/api/v1/partidas/", tags=["Partidas"])
async def listar_partidas(
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
        text(f"""
            SELECT * FROM presupuesto_base
            {where}
            ORDER BY id
            OFFSET :skip LIMIT :limit
        """),
        params
    ).fetchall()

    items = [dict(r._mapping) for r in rows]
    return {"total": total, "skip": skip, "limit": limit, "items": items}


# ── MACRO ─────────────────────────────────────────────────────────────────────

@app.get("/api/v1/macro/series", tags=["Macro"])
async def macro_series():
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
    tc_usd = _get_tc_usd(db)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(
                "https://api.bcra.gob.ar/estadisticas/v3.0/monetarias",
                params={"idVariable": 15, "limit": 24},
            )
            data       = r.json() if r.status_code == 200 else {}
            resultados = data.get("results", [])
            if not resultados:
                raise ValueError("Sin datos BCRA")

            ultimo    = resultados[-1]
            bm_actual = float(ultimo.get("valor", 0)) * 1e6
            inicio_dato = next(
                (x for x in resultados if str(x.get("fecha", "")).startswith("2023-12")),
                resultados[0],
            )
            bm_inicio    = float(inicio_dato.get("valor", 0)) * 1e6
            var_pct      = (bm_actual / bm_inicio - 1) * 100 if bm_inicio else 0
            multiplicador = bm_actual / bm_inicio if bm_inicio else 1

            serie_mensual = []
            for item in resultados:
                bm   = float(item.get("valor", 0)) * 1e6
                mult = bm / bm_inicio if bm_inicio else 1
                serie_mensual.append({
                    "label":   str(item.get("fecha", ""))[:7],
                    "bm_bill": round(bm / 1e12, 2),
                    "var_pct": round((bm / bm_inicio - 1) * 100, 1) if bm_inicio else 0,
                    "mult":    round(mult, 2),
                })

            return {
                "inicio":        {"label": str(inicio_dato.get("fecha", ""))[:7], "bm_billones": round(bm_inicio / 1e12, 2)},
                "actual":        {"label": str(ultimo.get("fecha", ""))[:7], "bm_billones": round(bm_actual / 1e12, 2), "bm_usd_mm": round(bm_actual / tc_usd / 1e6, 0) if tc_usd else None},
                "variacion_pct": round(var_pct, 1),
                "multiplicador": round(multiplicador, 2),
                "serie_mensual": serie_mensual,
            }
        except Exception as e:
            return {"error": f"No se pudo obtener Base Monetaria: {e}"}


# ── NORMATIVA ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/normativa/", tags=["Normativa"])
async def listar_normativa(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    items = db.query(models.Norma).offset(skip).limit(limit).all()
    total = db.query(func.count(models.Norma.id)).scalar()
    return {"total": total, "items": items}


@app.get("/api/v1/normativa/{norma_id}", tags=["Normativa"])
async def detalle_normativa(norma_id: int, db: Session = Depends(get_db)):
    norma = db.query(models.Norma).filter(models.Norma.id == norma_id).first()
    if not norma:
        raise HTTPException(status_code=404, detail="Norma no encontrada")
    return norma


@app.get("/api/v1/normativa/{norma_id}/partidas", tags=["Normativa"])
async def partidas_por_norma(norma_id: int, db: Session = Depends(get_db)):
    norma = db.query(models.Norma).filter(models.Norma.id == norma_id).first()
    if not norma:
        raise HTTPException(status_code=404, detail="Norma no encontrada")
    return {"norma_id": norma_id, "partidas": norma.partidas}


# ── COMPARATIVA ───────────────────────────────────────────────────────────────

@app.get("/api/v1/comparativa/", tags=["Comparativa"])
async def comparativa(db: Session = Depends(get_db)):
    analizador = AnalizadorPresupuestario(db)
    ipc_factor = _get_ipc_factor(db)
    tc_usd     = _get_tc_usd(db)
    return analizador.comparativa_total(ipc_factor=ipc_factor, tc_usd=tc_usd)


# ── SCRAPING / HEALTH ─────────────────────────────────────────────────────────

@app.post("/api/v1/scrape/trigger", tags=["Scraping"])
async def trigger_scrape(background_tasks: BackgroundTasks):
    from scripts.scraper_bora import scrape_bora
    background_tasks.add_task(scrape_bora)
    return {"status": "scraping iniciado", "timestamp": datetime.utcnow().isoformat()}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "version": "2.3.0", "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)