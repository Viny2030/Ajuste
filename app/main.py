# app/main.py
"""
FastAPI — Monitor de Ajuste Presupuestario (MAP)
Endpoints completos:
  /api/v1/partidas/                   → Listado con filtros
  /api/v1/analisis/ajuste/{id}        → Ajuste real de una partida
  /api/v1/analisis/ranking            → Top N partidas más ajustadas
  /api/v1/analisis/por-inciso         → Ajuste agregado por inciso
  /api/v1/macro/series                → IPC y USD desde BCRA
  /api/v1/macro/base-monetaria        → Base Monetaria en vivo BCRA
  /api/v1/normativa/                  → Listado de normas JGM
  /api/v1/normativa/{norma_id}        → Detalle de una norma
  /api/v1/normativa/{norma_id}/partidas → Partidas afectadas por norma
  /api/v1/comparativa/                → Gasto nominal vs real vs inflación vs USD
  /api/v1/scrape/trigger              → Dispara scraper BORA (async)
  /api/social/kpis                    → KPIs sanitarios (mortalidad, adultos mayores, suicidios)
  /api/social/status                  → Health-check datos sociales
"""
import asyncio
import uvicorn
import httpx
from datetime import datetime, date, timedelta
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import models, schemas
from app.database.session import SessionLocal, engine
from app.core.engine import AnalizadorPresupuestario, cargar_macro_indices
from app.core.viz import generar_grafico_ajuste
from scripts.social.router_social import router as social_router

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Monitor de Ajuste Presupuestario (MAP)",
    description=(
        "Análisis del ajuste presupuestario 2023–2026: "
        "partidas vs IPC y cotización del dólar. "
        "Decisiones Administrativas del Jefe de Gabinete."
    ),
    version="2.0.0",
)

# Servir archivos estáticos (dashboard)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ── Routers ─────────────────────────────────────────────────────
app.include_router(social_router)


@app.get("/dashboard", tags=["Home"], include_in_schema=False)
async def dashboard():
    """Dashboard visual del ajuste presupuestario."""
    path = os.path.join(os.path.dirname(__file__), "static", "main.html")
    return FileResponse(path)
@app.get("/manual", tags=["Home"], include_in_schema=False)
async def manual():
    """Manual técnico del MAP."""
    path = os.path.join(os.path.dirname(__file__), "static", "manual.html")
    return FileResponse(path)

# ── Dependencia DB ──────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── HOME ────────────────────────────────────────────────────────

@app.get("/", tags=["Home"])
async def root():
    macro = cargar_macro_indices()
    return {
        "sistema": "Monitor de Ajuste Presupuestario (MAP)",
        "version": "2.0.0",
        "estado": "Online",
        "punto_base": "Enero 2023",
        "factor_ipc_acumulado": round(macro["factor_deflactacion"], 4),
        "usd_oficial_actual": macro["usd_actual"],
        "servidor_tiempo": datetime.now().isoformat(),
    }


# ── JURISDICCIONES ──────────────────────────────────────────────

@app.get(
    "/api/v1/jurisdicciones",
    tags=["Presupuesto"],
)
def listar_jurisdicciones(db: Session = Depends(get_db)):
    """Lista todas las jurisdicciones con ID, descripcion y totales presupuestados."""
    rows = (
        db.query(
            models.PresupuestoBase.jurisdiccion_id,
            models.PresupuestoBase.jurisdiccion_desc,
            func.sum(models.PresupuestoBase.monto_original).label("total_original"),
            func.sum(models.PresupuestoBase.monto_vigente).label("total_vigente"),
        )
        .group_by(
            models.PresupuestoBase.jurisdiccion_id,
            models.PresupuestoBase.jurisdiccion_desc,
        )
        .order_by(func.sum(models.PresupuestoBase.monto_original).desc())
        .all()
    )
    return [
        {
            "jurisdiccion_id": r.jurisdiccion_id,
            "jurisdiccion_desc": r.jurisdiccion_desc,
            "total_original": round(r.total_original or 0),
            "total_vigente": round(r.total_vigente or 0),
        }
        for r in rows
    ]


@app.get(
    "/api/v1/jurisdicciones/{jurisdiccion_id}/programas",
    tags=["Presupuesto"],
)
def programas_por_jurisdiccion(
    jurisdiccion_id: str,
    inciso_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Programas de una jurisdiccion con ajuste real e impacto en USD al TC historico."""
    jid = jurisdiccion_id.lstrip("0") or "0"
    q = db.query(models.PresupuestoBase).filter(
        models.PresupuestoBase.jurisdiccion_id == jid
    )
    if inciso_id:
        q = q.filter(models.PresupuestoBase.inciso_id == inciso_id)
    programas = q.limit(500).all()
    if not programas:
        raise HTTPException(404, detail=f"Jurisdiccion '{jurisdiccion_id}' no encontrada")
    all_mods = db.query(models.ModificacionPresupuestaria).all()
    mods_map: dict = {}
    for m in all_mods:
        mods_map.setdefault(m.programa_id, []).append(m)
    analizador = AnalizadorPresupuestario(db)
    resultado = []
    for prog in programas:
        mods = mods_map.get(prog.programa_id, [])
        r = analizador.calcular_variacion_real(prog, mods)
        if r:
            r["inciso_id"] = prog.inciso_id
            r["inciso_desc"] = prog.inciso_desc
            resultado.append(r)
    return sorted(resultado, key=lambda x: x["variacion_real_pct"])


# ── PARTIDAS ────────────────────────────────────────────────────

@app.get(
    "/api/v1/partidas",
    response_model=List[schemas.PartidaResumen],
    tags=["Presupuesto"],
)
def listar_partidas(
    skip: int = 0,
    limit: int = 100,
    jurisdiccion_id: Optional[str] = Query(None, description="Filtrar por código de jurisdicción"),
    inciso_id: Optional[str] = Query(None, description="Filtrar por inciso (1=Personal, 2=Bienes, etc.)"),
    fuente: Optional[str] = Query(None, description="Filtrar por fuente de financiamiento"),
    db: Session = Depends(get_db),
):
    """Listado de partidas con filtros opcionales."""
    q = db.query(models.PresupuestoBase)
    if jurisdiccion_id:
        q = q.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
    if inciso_id:
        q = q.filter(models.PresupuestoBase.inciso_id == inciso_id)
    if fuente:
        q = q.filter(models.PresupuestoBase.fuente_financiamiento_id == fuente)
    return q.offset(skip).limit(limit).all()


# ── ANÁLISIS DE AJUSTE ──────────────────────────────────────────

@app.get(
    "/api/v1/analisis/ajuste/{programa_id}",
    response_model=schemas.AjustePartida,
    tags=["Analítica"],
)
def ajuste_por_programa(programa_id: str, db: Session = Depends(get_db)):
    """Calcula la variación real (deflactada por IPC) de un programa."""
    base = (
        db.query(models.PresupuestoBase)
        .filter(models.PresupuestoBase.programa_id == programa_id)
        .first()
    )
    if not base:
        raise HTTPException(404, detail=f"Programa '{programa_id}' no encontrado en base 2023")

    mods = (
        db.query(models.ModificacionPresupuestaria)
        .filter(models.ModificacionPresupuestaria.programa_id == programa_id)
        .all()
    )
    analizador = AnalizadorPresupuestario(db)
    return analizador.calcular_variacion_real(base, mods)


@app.get(
    "/api/v1/analisis/ranking",
    tags=["Analítica"],
)
def ranking_ajuste(
    top_n: int = Query(20, le=500),
    jurisdiccion_id: Optional[str] = None,
    inciso_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    macro = cargar_macro_indices()
    factor = macro["factor_deflactacion"]
    usd_actual = macro.get("usd_actual")
    TC_ENE2023 = 187.0
    tc_actual  = usd_actual if usd_actual else TC_ENE2023 * factor

    q_base = db.query(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_id,
        models.PresupuestoBase.jurisdiccion_desc,
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
        func.sum(models.PresupuestoBase.monto_original).label("original"),
    ).filter(models.PresupuestoBase.ejercicio == 2023).group_by(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_id,
        models.PresupuestoBase.jurisdiccion_desc,
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
    )
    if jurisdiccion_id:
        q_base = q_base.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
    if inciso_id:
        q_base = q_base.filter(models.PresupuestoBase.inciso_id == inciso_id)

    base_map = {
        (str(r.programa_id).strip(), str(r.inciso_id or '').strip()): r
        for r in q_base.all()
    }

    vigente_map = {}
    for anio in [2024, 2025, 2026]:
        q_vig = db.query(
            models.PresupuestoBase.programa_id,
            models.PresupuestoBase.inciso_id,
            func.sum(models.PresupuestoBase.monto_vigente).label("vigente"),
        ).filter(models.PresupuestoBase.ejercicio == anio).group_by(
            models.PresupuestoBase.programa_id,
            models.PresupuestoBase.inciso_id,
        )
        if jurisdiccion_id:
            q_vig = q_vig.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
        if inciso_id:
            q_vig = q_vig.filter(models.PresupuestoBase.inciso_id == inciso_id)
        for r in q_vig.all():
            key = (str(r.programa_id).strip(), str(r.inciso_id or '').strip())
            vigente_map[key] = float(r.vigente or 0) * 1_000_000

    cant_map = {
        (str(r.programa_id).strip(), str(r.inciso_id or '').strip()): int(r.cant or 0)
        for r in db.query(
            models.ModificacionPresupuestaria.programa_id,
            models.ModificacionPresupuestaria.inciso_id,
            func.count(models.ModificacionPresupuestaria.id).label("cant"),
        ).filter(
            models.ModificacionPresupuestaria.norma_id.like("CREDITO-VIGENTE-%")
        ).group_by(
            models.ModificacionPresupuestaria.programa_id,
            models.ModificacionPresupuestaria.inciso_id,
        ).all()
    }

    resultado = []
    for key, r in base_map.items():
        if not r.original or r.original < 100_000_000:
            continue

        vigente_real = vigente_map.get(key)
        if vigente_real is None or vigente_real == 0:
            continue

        var_nom    = ((vigente_real / r.original) - 1) * 100
        real_pesos = vigente_real / factor
        var_real   = ((real_pesos / r.original) - 1) * 100
        abs_nom    = vigente_real - r.original
        abs_real   = real_pesos - r.original

        original_usd = round(r.original / TC_ENE2023, 2)
        vigente_usd  = round(vigente_real / tc_actual, 2) if tc_actual else None
        abs_usd      = round(vigente_usd - original_usd, 2) if vigente_usd is not None else None
        var_usd_pct  = round(((vigente_usd / original_usd) - 1) * 100, 2) if (vigente_usd and original_usd) else None
        # Filtrar outliers que distorsionan promedios sectoriales
        if var_real > 500 or var_real < -99.5:
            continue
        resultado.append({
            "programa_id":               r.programa_id,
            "programa_desc":             r.programa_desc,
            "jurisdiccion":              r.jurisdiccion_desc,
            "jurisdiccion_id":           str(r.jurisdiccion_id or "").strip(),
            "inciso_id":                 r.inciso_id,
            "inciso_desc":               r.inciso_desc,
            "monto_original":            round(r.original, 2),
            "monto_vigente_nominal":     round(vigente_real, 2),
            "monto_real_en_moneda_2023": round(real_pesos, 2),
            "ajuste_nominal_pct":        round(var_nom, 2),
            "ajuste_nominal_abs":        round(abs_nom, 2),
            "ajuste_real_pct":           round(var_real, 2),
            "ajuste_real_abs":           round(abs_real, 2),
            "ajuste_usd_pct":            var_usd_pct,
            "ajuste_usd_abs":            abs_usd,
            "monto_original_usd":        original_usd,
            "monto_vigente_usd":         vigente_usd,
            "tc_ene2023":                TC_ENE2023,
            "tc_actual":                 round(tc_actual, 2) if tc_actual else None,
            "licuacion_pct":             round(var_nom - var_real, 2),
            "cantidad_modificaciones":   cant_map.get(key, 0),
            "estado_ajuste":             "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
            "variacion_nominal_pct":     round(var_nom, 2),
            "variacion_real_pct":        round(var_real, 2),
        })

    resultado.sort(key=lambda x: x["variacion_real_pct"])
    return resultado[:top_n]


@app.get(
    "/api/v1/analisis/por-inciso",
    tags=["Analítica"],
)
def ajuste_por_inciso(db: Session = Depends(get_db)):
    """
    Agregado del ajuste real por tipo de gasto (inciso).
    Original: ejercicio 2023 en pesos completos → normalizado a millones.
    Vigente:  ejercicio más reciente disponible (2026 > 2025 > 2024), en millones × 1_000_000.
    """
    macro = cargar_macro_indices()
    factor = macro["factor_deflactacion"]

    q_orig = db.query(
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
        func.sum(models.PresupuestoBase.monto_original).label("total_original"),
    ).filter(
        models.PresupuestoBase.ejercicio == 2023
    ).group_by(
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
    ).all()

    orig_map = {
        str(r.inciso_id or "").strip(): {
            "inciso_desc": r.inciso_desc,
            "total_original": float(r.total_original or 0),
        }
        for r in q_orig
    }

    vigente_map = {}
    for anio in [2024, 2025, 2026]:
        q_vig = db.query(
            models.PresupuestoBase.inciso_id,
            func.sum(models.PresupuestoBase.monto_vigente).label("total_vigente"),
        ).filter(
            models.PresupuestoBase.ejercicio == anio
        ).group_by(
            models.PresupuestoBase.inciso_id,
        ).all()
        for r in q_vig:
            key = str(r.inciso_id or "").strip()
            vigente_map[key] = float(r.total_vigente or 0) * 1_000_000

    resultado = []
    for inciso_id, orig in orig_map.items():
        total_orig = orig["total_original"]
        total_vig  = vigente_map.get(inciso_id)

        if not total_orig or total_orig == 0 or not total_vig:
            continue

        real     = total_vig / factor
        var_nom  = ((total_vig  / total_orig) - 1) * 100
        var_real = ((real       / total_orig) - 1) * 100

        resultado.append({
            "inciso_id":              inciso_id,
            "inciso_desc":            orig["inciso_desc"],
            "total_original":         round(total_orig, 2),
            "total_vigente":          round(total_vig,  2),
            "total_real_moneda_2023": round(real,       2),
            "variacion_nominal_pct":  round(var_nom,    2),
            "variacion_real_pct":     round(var_real,   2),
            "licuacion_pct":          round(var_nom - var_real, 2),
            "estado": "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
        })

    return sorted(resultado, key=lambda x: x["variacion_real_pct"])


# ── MACRO: IPC y USD ─────────────────────────────────────────────

@app.get(
    "/api/v1/macro/series",
    tags=["Macro"],
)
def series_macro(db: Session = Depends(get_db)):
    """Retorna las series mensuales de IPC y USD oficial desde Enero 2023."""
    analizador = AnalizadorPresupuestario(db)
    return analizador.get_serie_macro()


# ── MACRO: BASE MONETARIA (BCRA API en vivo) ─────────────────────

BCRA_MONETARIAS   = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
BCRA_CAMBIARIAS   = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones"
ID_BASE_MONETARIA = 15
FECHA_ASUNCION    = "2023-12-07"
BM_ASUNCION_MM    = 10_124_959   # millones ARS — BCRA 07/12/2023

TC_USD_FALLBACK   = 1_150.0


def _label_mes(fecha_str: str) -> str:
    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    try:
        p = fecha_str.split("-")
        return f"{meses[int(p[1])-1]} {p[0]}"
    except Exception:
        return fecha_str


async def _obtener_tc_usd(client: httpx.AsyncClient) -> tuple[float | None, bool]:
    hoy      = date.today().isoformat()
    hace_30d = (date.today() - timedelta(days=30)).isoformat()

    urls_a_probar = [
        (
            f"{BCRA_CAMBIARIAS}/USD",
            {"fechadesde": hace_30d, "fechahasta": hoy, "limit": 10},
            lambda data: next(
                (
                    det.get("tipoCotizacion") or det.get("tipoPase")
                    for row in data.get("results", [])
                    for det in (row.get("detalle") or [])
                    if (det.get("tipoCotizacion") or det.get("tipoPase") or 0) > 0
                ),
                None,
            ),
        ),
        (
            "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/4",
            {"desde": hace_30d, "hasta": hoy, "limit": 5},
            lambda data: next(
                (row["valor"] for row in reversed(data.get("results", [{}])[0].get("detalle", []))),
                None,
            ),
        ),
    ]

    for url, params, extractor in urls_a_probar:
        try:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                tc = extractor(r.json())
                if tc and float(tc) > 0:
                    return float(tc), False
        except Exception:
            continue

    return TC_USD_FALLBACK, True


@app.get(
    "/api/v1/macro/base-monetaria",
    tags=["Macro"],
    summary="Base Monetaria — datos en vivo BCRA",
)
async def base_monetaria():
    hoy = date.today().isoformat()

    async with httpx.AsyncClient(timeout=12, verify=False) as client:

        resp = await client.get(
            f"{BCRA_MONETARIAS}/{ID_BASE_MONETARIA}",
            params={"desde": FECHA_ASUNCION, "hasta": hoy, "limit": 3000},
        )
        if resp.status_code != 200:
            raise HTTPException(502, "Error al consultar API BCRA (base monetaria)")

        detalle     = resp.json()["results"][0]["detalle"]
        detalle_asc = list(reversed(detalle))

        ultimo    = detalle_asc[-1]
        bm_actual = ultimo["valor"]
        fecha_ult = ultimo["fecha"]

        tc_usd, tc_es_fallback = await _obtener_tc_usd(client)

    var_pct = round((bm_actual / BM_ASUNCION_MM - 1) * 100, 1)
    mult    = round(bm_actual / BM_ASUNCION_MM, 2)
    bm_usd_mm = round(bm_actual / tc_usd, 0)

    serie = []
    meses_vistos = set()
    for row in detalle_asc:
        mes = row["fecha"][:7]
        if mes not in meses_vistos:
            meses_vistos.add(mes)
            serie.append({
                "fecha":   row["fecha"],
                "label":   _label_mes(row["fecha"]),
                "bm_bill": round(row["valor"] / 1_000_000, 1),
                "var_pct": round((row["valor"] / BM_ASUNCION_MM - 1) * 100, 1),
                "mult":    round(row["valor"] / BM_ASUNCION_MM, 2),
            })

    tc_nota = (
        f"TC USD: ${tc_usd:,.0f} (oficial BNA)"
        if not tc_es_fallback
        else f"TC USD: ${tc_usd:,.0f} (referencia aproximada — API BCRA no disponible)"
    )

    return {
        "inicio": {
            "fecha":       FECHA_ASUNCION,
            "label":       "07/12/2023",
            "bm_billones": round(BM_ASUNCION_MM / 1_000_000, 1),
        },
        "actual": {
            "fecha":       fecha_ult,
            "label":       _label_mes(fecha_ult),
            "bm_billones": round(bm_actual / 1_000_000, 1),
            "bm_usd_mm":   bm_usd_mm,
            "tc_usd":      tc_usd,
            "tc_es_fallback": tc_es_fallback,
        },
        "variacion_pct": var_pct,
        "multiplicador":  mult,
        "nota": (
            f"La cantidad de dinero circulante (más los encajes bancarios) "
            f"se multiplicó por {mult}x respecto al valor que recibió "
            f"la administración actual al asumir en diciembre de 2023. "
            f"{tc_nota}."
        ),
        "serie_mensual": serie,
    }


# ── NORMATIVA JGM ────────────────────────────────────────────────

@app.get(
    "/api/v1/normativa",
    response_model=List[schemas.NormaResumen],
    tags=["Normativa"],
)
def listar_normas(
    tipo_accion: Optional[str] = Query(None, description="REDUCCION | REASIGNACION | AMPLIACION"),
    anio: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.NormaJGM)
    if tipo_accion:
        q = q.filter(models.NormaJGM.tipo_accion == tipo_accion.upper())
    if anio:
        q = q.filter(models.NormaJGM.anio == anio)
    return q.order_by(models.NormaJGM.fecha_publicacion.desc()).all()


@app.get(
    "/api/v1/normativa/{norma_id}",
    response_model=schemas.NormaResumen,
    tags=["Normativa"],
)
def detalle_norma(norma_id: str, db: Session = Depends(get_db)):
    norma = (
        db.query(models.NormaJGM)
        .filter(models.NormaJGM.norma_id == norma_id)
        .first()
    )
    if not norma:
        raise HTTPException(404, detail=f"Norma '{norma_id}' no encontrada")
    return norma


@app.get(
    "/api/v1/normativa/{norma_id}/partidas",
    tags=["Normativa"],
)
def partidas_por_norma(norma_id: str, db: Session = Depends(get_db)):
    mods = (
        db.query(models.ModificacionPresupuestaria)
        .filter(models.ModificacionPresupuestaria.norma_id == norma_id)
        .all()
    )
    if not mods:
        raise HTTPException(404, detail=f"No hay partidas registradas para '{norma_id}'")
    analizador = AnalizadorPresupuestario(db)
    return [analizador.cruce_norma_inflacion(m) for m in mods]


# ── COMPARATIVA ──────────────────────────────────────────────────

@app.get(
    "/api/v1/comparativa",
    tags=["Analítica"],
)
def comparativa_global(
    jurisdiccion_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.ModificacionPresupuestaria)
    if jurisdiccion_id:
        q = q.join(models.PresupuestoBase).filter(
            models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id
        )
    mods = q.order_by(models.ModificacionPresupuestaria.fecha_boletin).all()
    analizador = AnalizadorPresupuestario(db)
    macro = analizador.get_serie_macro()

    ipc_idx = {}
    for row in macro["ipc"]:
        mes = row["fecha"][:7]
        ipc_idx[mes] = row.get("ipc_acum_vs_ene23", 1.0)

    usd_idx = {}
    for row in macro["usd_oficial"]:
        mes = row["fecha"][:7]
        usd_idx[mes] = row.get("usd_oficial")

    por_mes = {}
    for m in mods:
        if not m.fecha_boletin:
            continue
        mes = m.fecha_boletin.strftime("%Y-%m")
        if mes not in por_mes:
            por_mes[mes] = {"aumento": 0.0, "reduccion": 0.0}
        por_mes[mes]["aumento"] += m.aumento or 0.0
        por_mes[mes]["reduccion"] += m.reduccion or 0.0

    resultado = []
    for mes, montos in sorted(por_mes.items()):
        factor = ipc_idx.get(mes, analizador.ipc_acumulado)
        neto = montos["aumento"] - montos["reduccion"]
        resultado.append({
            "periodo": mes,
            "modificacion_neta_nominal": round(neto, 2),
            "modificacion_neta_real_moneda_2023": round(neto / factor, 2),
            "factor_ipc_acumulado": round(factor, 4),
            "usd_oficial": usd_idx.get(mes),
            "aumento": round(montos["aumento"], 2),
            "reduccion": round(montos["reduccion"], 2),
        })

    return resultado


# ── VISUALIZACIÓN ─────────────────────────────────────────────────

@app.get(
    "/api/v1/graficos/ajuste/{programa_id}",
    response_class=HTMLResponse,
    tags=["Visualización"],
)
def grafico_ajuste(programa_id: str, db: Session = Depends(get_db)):
    base = (
        db.query(models.PresupuestoBase)
        .filter(models.PresupuestoBase.programa_id == programa_id)
        .first()
    )
    if not base:
        raise HTTPException(404, detail="No hay datos para graficar")
    mods = (
        db.query(models.ModificacionPresupuestaria)
        .filter(models.ModificacionPresupuestaria.programa_id == programa_id)
        .all()
    )
    analizador = AnalizadorPresupuestario(db)
    ajuste = analizador.calcular_variacion_real(base, mods)
    return generar_grafico_ajuste(
        nombre_programa=base.programa_desc,
        original=base.monto_original,
        vigente=ajuste["monto_vigente_nominal"],
        real=ajuste["monto_real_en_moneda_2023"],
    )


# ── EVOLUCIÓN REAL POR PERÍODO ───────────────────────────────────

@app.get(
    "/api/v1/evolucion-real",
    tags=["Analítica"],
    summary="Evolución real del gasto por período — acumulado deflactado por IPC",
)
def evolucion_real(
    jurisdiccion_id: Optional[str] = None,
    inciso_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Devuelve la evolución anual del gasto público en pesos constantes (enero 2023).
    Para cada ejercicio disponible calcula:
      - total nominal (monto_vigente en millones × 1_000_000 para 2024+, pesos completos para 2023)
      - total real (deflactado por IPC acumulado desde ene-2023)
      - variación real % respecto al ejercicio anterior
    """
    macro = cargar_macro_indices()
    factor_total = macro["factor_deflactacion"]

    # ── Calcular factores IPC al cierre de cada año desde la DB ──
    # La tabla macro_indices guarda 'IPC_variacion_mensual' (variación % mensual)
    # Factor acumulado = producto de (1 + var/100) desde ene-2023
    FACTORES_FALLBACK = {
        2023: 1.0,
        2024: 3.05,   # dic-2023: inflación 2023 ~211%
        2025: 5.60,   # dic-2024: inflación 2024 ~118%
        2026: factor_total,
    }

    factores_anio = dict(FACTORES_FALLBACK)
    fuentes_anio = {2023: "base", 2024: "estimado_indec", 2025: "estimado_indec", 2026: "serie_bcra"}

    try:
        ipc_rows = (
            db.query(models.MacroIndice)
            .filter(
                models.MacroIndice.indicador == "IPC_variacion_mensual",
                models.MacroIndice.fecha >= "2023-01-01",
            )
            .order_by(models.MacroIndice.fecha)
            .all()
        )
        if ipc_rows:
            factor_acum = 1.0
            ultimo_anio_visto = 2023
            for row in ipc_rows:
                try:
                    anio = row.fecha.year
                    factor_acum *= (1 + float(row.valor) / 100)
                    ultimo_anio_visto = anio
                    # Al final de cada año, guardar el factor acumulado
                    # (se sobreescribe en cada mes → queda el último mes del año)
                    if anio in factores_anio:
                        factores_anio[anio] = round(factor_acum, 4)
                        fuentes_anio[anio] = "db_ipc_mensual"
                except (AttributeError, TypeError, ValueError):
                    continue
            # 2026 siempre usa el factor_total del engine (más preciso, toma API en vivo)
            factores_anio[2026] = factor_total
            fuentes_anio[2026] = "serie_bcra"
    except Exception:
        pass  # si falla, se usan los fallbacks INDEC

    resultado = {}
    for anio in [2023, 2024, 2025, 2026]:
        q = db.query(
            func.sum(models.PresupuestoBase.monto_original if anio == 2023 else models.PresupuestoBase.monto_vigente).label("total"),
        ).filter(models.PresupuestoBase.ejercicio == anio)
        if jurisdiccion_id:
            q = q.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
        if inciso_id:
            q = q.filter(models.PresupuestoBase.inciso_id == inciso_id)
        row = q.first()
        total_raw = float(row.total or 0) if row else 0.0

        # Normalizar a pesos completos
        if anio == 2023:
            total_nominal = total_raw
        else:
            total_nominal = total_raw * 1_000_000

        factor = factores_anio.get(anio, factor_total)
        total_real = total_nominal / factor if factor else total_nominal

        resultado[anio] = {
            "ejercicio":      anio,
            "total_nominal":  round(total_nominal, 0),
            "total_real":     round(total_real, 0),
            "factor_ipc":     round(factor, 4),
            "factor_fuente":  fuentes_anio.get(anio, "estimado_indec"),
        }

    # Calcular variaciones YoY
    salida = []
    prev_real = None
    for anio in [2023, 2024, 2025, 2026]:
        d = resultado[anio]
        var_real_pct = None
        if prev_real and prev_real > 0:
            var_real_pct = round(((d["total_real"] / prev_real) - 1) * 100, 2)
        d["variacion_real_pct_yoy"] = var_real_pct
        prev_real = d["total_real"]
        salida.append(d)

    return salida






# ── ANÁLISIS SECTORIAL ────────────────────────────────────────────
# Compara gasto por jurisdicción entre 2023 y 2026 SIN cruzar por programa_id
# Resuelve: (1) migración jur 75→88, (2) inestabilidad de programa_id entre años

SECTORES_DEF = {
    "salud":        {"jur_2023": ["80"],      "jur_2026": ["80"],      "label": "Salud"},
    "jubilaciones": {"jur_2023": ["75","91"], "jur_2026": ["88","91"], "label": "Jubilaciones y Seguridad Social",
                     "excluir_jur91_prg": ["76","87"]},
    "ninez":        {"jur_2023": ["85"],      "jur_2026": ["88"],      "label": "Niñez y Desarrollo Social",
                     "prg_2023": ["47","46","53","57","58"],
                     "prg_2026": ["19","32","49"]},
    "obra-publica": {"jur_2023": ["64","57","65"], "jur_2026": ["64","57","65"], "label": "Obra Pública e Infraestructura"},
    "salario":      {"jur_2023": None, "jur_2026": None, "inciso": "1", "label": "Salario Público"},
    "educacion":    {"jur_2023": ["70"], "jur_2026": ["88"], "label": "Educación",
                     "prg_2026": ["26","29","40","49","23"]},
}


@app.get("/api/v1/analisis/sector", tags=["Analítica"],
         summary="Comparativa sectorial 2023 vs 2026 — agrega por jurisdicción, sin cruzar por programa_id")
def analisis_sector(
    sector: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    macro   = cargar_macro_indices()
    factor  = macro["factor_deflactacion"]
    TC_2023 = 187.0
    tc_act  = macro.get("usd_actual") or TC_2023 * factor

    sectores = {sector: SECTORES_DEF[sector]} if sector and sector in SECTORES_DEF else SECTORES_DEF

    resultado = []
    for sec_id, cfg in sectores.items():
        inciso = cfg.get("inciso")

        def _suma(anio: int) -> float:
            col  = models.PresupuestoBase.monto_original if anio == 2023 else models.PresupuestoBase.monto_vigente
            jurs = cfg.get("jur_2023") if anio == 2023 else cfg.get("jur_2026")
            prgs = cfg.get("prg_2023") if anio == 2023 else cfg.get("prg_2026")
            exc91 = cfg.get("excluir_jur91_prg", [])

            if inciso:
                # salario: toda la APN, filtrar por inciso
                q = db.query(func.sum(col)).filter(
                    models.PresupuestoBase.ejercicio == anio,
                    models.PresupuestoBase.inciso_id == inciso,
                )
                raw = float(q.scalar() or 0)
            elif jurs and "91" in jurs and exc91 and anio != 2023:
                # jubilaciones 2026: jur 88 + jur 91 sin subsidios energía
                jurs_sin91 = [j for j in jurs if j != "91"]
                q1 = db.query(func.sum(col)).filter(
                    models.PresupuestoBase.ejercicio == anio,
                    models.PresupuestoBase.jurisdiccion_id.in_(jurs_sin91),
                )
                if prgs: q1 = q1.filter(models.PresupuestoBase.programa_id.in_(prgs))
                q2 = db.query(func.sum(col)).filter(
                    models.PresupuestoBase.ejercicio == anio,
                    models.PresupuestoBase.jurisdiccion_id == "91",
                    ~models.PresupuestoBase.programa_id.in_(exc91),
                )
                raw = float(q1.scalar() or 0) + float(q2.scalar() or 0)
            else:
                q = db.query(func.sum(col)).filter(
                    models.PresupuestoBase.ejercicio == anio,
                    models.PresupuestoBase.jurisdiccion_id.in_(jurs),
                )
                if prgs: q = q.filter(models.PresupuestoBase.programa_id.in_(prgs))
                raw = float(q.scalar() or 0)

            return raw if anio == 2023 else raw * 1_000_000

        orig = _suma(2023)
        vig  = _suma(2026)
        if orig == 0:
            continue

        real      = vig / factor
        var_nom   = round(((vig  / orig) - 1) * 100, 2)
        var_real  = round(((real / orig) - 1) * 100, 2)
        orig_usd  = round(orig / TC_2023, 0)
        vig_usd   = round(vig  / tc_act,  0) if tc_act else None
        var_usd   = round(((vig_usd / orig_usd) - 1) * 100, 2) if (vig_usd and orig_usd) else None

        resultado.append({
            "sector_id":             sec_id,
            "sector_label":          cfg["label"],
            "monto_original_2023":   round(orig, 0),
            "monto_vigente_2026":    round(vig,  0),
            "monto_real_2026":       round(real, 0),
            "variacion_nominal_pct": var_nom,
            "variacion_real_pct":    var_real,
            "monto_original_usd":    orig_usd,
            "monto_vigente_usd":     vig_usd,
            "variacion_usd_pct":     var_usd,
            "factor_ipc":            round(factor, 4),
            "estado":                "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
        })

    return resultado


@app.post(
    "/api/v1/scrape/trigger",
    tags=["Admin"],
    status_code=202,
)
async def trigger_scrape(background_tasks: BackgroundTasks, desde: str = "01/01/2023"):
    from app.core.daily_sync import sincronizar
    background_tasks.add_task(sincronizar, desde=desde)
    return {"status": "accepted", "mensaje": f"Scraping iniciado desde {desde}"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)