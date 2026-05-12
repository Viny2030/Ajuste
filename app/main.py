# app/main.py
"""
FastAPI — Monitor de Ajuste Presupuestario (MAP)
Endpoints completos:
  /api/v1/partidas/                   → Listado con filtros
  /api/v1/analisis/ajuste/{id}        → Ajuste real de una partida
  /api/v1/analisis/ranking            → Top N partidas más ajustadas
  /api/v1/analisis/por-inciso         → Ajuste agregado por inciso
  /api/v1/macro/series                → IPC y USD desde BCRA
  /api/v1/normativa/                  → Listado de normas JGM
  /api/v1/normativa/{norma_id}        → Detalle de una norma
  /api/v1/normativa/{norma_id}/partidas → Partidas afectadas por norma
  /api/v1/comparativa/                → Gasto nominal vs real vs inflación vs USD
  /api/v1/scrape/trigger              → Dispara scraper BORA (async)
"""
import asyncio
import uvicorn
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import models, schemas
from app.database.session import SessionLocal, engine
from app.core.engine import AnalizadorPresupuestario, cargar_macro_indices
from app.core.viz import generar_grafico_ajuste

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
    top_n: int = Query(20, le=100),
    jurisdiccion_id: Optional[str] = None,
    inciso_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Top N programas con mayor reducción real del presupuesto."""
    q = db.query(models.PresupuestoBase)
    if jurisdiccion_id:
        q = q.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
    if inciso_id:
        q = q.filter(models.PresupuestoBase.inciso_id == inciso_id)
    programas = q.all()

    # Cargar TODAS las modificaciones sin filtro IN (evita "too many SQL variables" en SQLite)
    # Como la tabla de modificaciones empieza vacía (aún no se corrió el scraper),
    # esto es O(1) por ahora y escala bien cuando haya datos.
    all_mods = db.query(models.ModificacionPresupuestaria).all()
    mods_map: dict = {}
    for m in all_mods:
        mods_map.setdefault(m.programa_id, []).append(m)

    analizador = AnalizadorPresupuestario(db)
    return analizador.ranking_ajuste(programas, mods_map, top_n)


@app.get(
    "/api/v1/analisis/por-inciso",
    tags=["Analítica"],
)
def ajuste_por_inciso(db: Session = Depends(get_db)):
    """
    Agregado del ajuste real por tipo de gasto (inciso):
    1-Personal, 2-Bienes, 3-Servicios, 4-Transferencias, 5-Inversión, etc.
    """
    rows = (
        db.query(
            models.PresupuestoBase.inciso_id,
            models.PresupuestoBase.inciso_desc,
            func.sum(models.PresupuestoBase.monto_original).label("total_original"),
            func.sum(models.PresupuestoBase.monto_vigente).label("total_vigente"),
        )
        .group_by(
            models.PresupuestoBase.inciso_id,
            models.PresupuestoBase.inciso_desc,
        )
        .all()
    )

    macro = cargar_macro_indices()
    factor = macro["factor_deflactacion"]
    resultado = []
    for r in rows:
        real = r.total_vigente / factor
        var_real = ((real / r.total_original) - 1) * 100 if r.total_original else 0
        resultado.append({
            "inciso_id": r.inciso_id,
            "inciso_desc": r.inciso_desc,
            "total_original": round(r.total_original, 2),
            "total_vigente": round(r.total_vigente, 2),
            "total_real_moneda_2023": round(real, 2),
            "variacion_real_pct": round(var_real, 2),
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
    """Listado de Decisiones Administrativas del JGM scrapeadas del BORA."""
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
    """Detalle de una norma específica (ej: DA-58-2024)."""
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
def partidas_por_norma(
    norma_id: str,
    db: Session = Depends(get_db),
):
    """Partidas presupuestarias afectadas por una Decisión Administrativa."""
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
    """
    Cuadro comparativo: Gasto Nominal vs Gasto Real vs IPC Acumulado vs USD.
    Agrupado mensualmente por fecha de las modificaciones.
    """
    q = db.query(models.ModificacionPresupuestaria)
    if jurisdiccion_id:
        # join para filtrar por jurisdiccion
        q = q.join(models.PresupuestoBase).filter(
            models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id
        )

    mods = q.order_by(models.ModificacionPresupuestaria.fecha_boletin).all()
    analizador = AnalizadorPresupuestario(db)
    macro = analizador.get_serie_macro()

    # Indexar IPC por mes
    ipc_idx = {}
    for row in macro["ipc"]:
        mes = row["fecha"][:7]  # YYYY-MM
        ipc_idx[mes] = row.get("ipc_acum_vs_ene23", 1.0)

    usd_idx = {}
    for row in macro["usd_oficial"]:
        mes = row["fecha"][:7]
        usd_idx[mes] = row.get("usd_oficial")

    # Agrupar modificaciones por mes
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
    """Gráfico interactivo (Plotly): Nominal Original vs Vigente vs Real."""
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


# ── SCRAPE TRIGGER (background) ──────────────────────────────────

@app.post(
    "/api/v1/scrape/trigger",
    tags=["Admin"],
    status_code=202,
)
async def trigger_scrape(background_tasks: BackgroundTasks, desde: str = "01/01/2023"):
    """
    Dispara el scraper del BORA en background.
    Retorna 202 Accepted inmediatamente.
    """
    from app.core.daily_sync import sincronizar
    background_tasks.add_task(sincronizar, desde=desde)
    return {"status": "accepted", "mensaje": f"Scraping iniciado desde {desde}"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
