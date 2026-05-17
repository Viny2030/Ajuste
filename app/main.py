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

@app.get("/dashboard", tags=["Home"], include_in_schema=False)
async def dashboard():
    """Dashboard visual del ajuste presupuestario."""
    path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
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
    top_n: int = Query(20, le=500),   # subimos el límite: los tabs sectoriales necesitan todos
    jurisdiccion_id: Optional[str] = None,
    inciso_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    macro = cargar_macro_indices()
    factor = macro["factor_deflactacion"]

    # ── 1. Base 2023: original + vigente base agrupados por programa ──────────
    q = db.query(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_desc,
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
        func.sum(models.PresupuestoBase.monto_original).label("original"),
        func.sum(models.PresupuestoBase.monto_vigente).label("vigente_base"),
    ).group_by(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_desc,
        models.PresupuestoBase.inciso_id,
        models.PresupuestoBase.inciso_desc,
    )
    if jurisdiccion_id:
        q = q.filter(models.PresupuestoBase.jurisdiccion_id == jurisdiccion_id)
    if inciso_id:
        q = q.filter(models.PresupuestoBase.inciso_id == inciso_id)

    base_rows = q.all()

    # ── 2. Modificaciones sintéticas (CREDITO-VIGENTE-*): neto por programa ──
    # sum(monto_neto) = vigente_reciente - vigente_2023, en pesos nominales
    mods_q = (
        db.query(
            models.ModificacionPresupuestaria.programa_id,
            models.ModificacionPresupuestaria.inciso_id,
            func.sum(models.ModificacionPresupuestaria.monto_neto).label("neto"),
            func.count(models.ModificacionPresupuestaria.id).label("cant"),
        )
        .filter(
            models.ModificacionPresupuestaria.norma_id.like("CREDITO-VIGENTE-%")
        )
        .group_by(
            models.ModificacionPresupuestaria.programa_id,
            models.ModificacionPresupuestaria.inciso_id,
        )
    )
    # key: (programa_id, inciso_id) → {neto, cant}
    mods_map = {
        (str(r.programa_id), str(r.inciso_id or "")): {
            "neto": float(r.neto or 0),
            "cant": int(r.cant or 0),
        }
        for r in mods_q.all()
    }

    # ── 3. Calcular variaciones usando vigente real ───────────────────────────
    resultado = []
    for r in base_rows:
        if not r.original or r.original == 0:
            continue

        key = (str(r.programa_id), str(r.inciso_id or ""))
        mod = mods_map.get(key, {"neto": 0, "cant": 0})

        # vigente real = base 2023 + diferencia neta del sync
        # Si no hay modificaciones sintéticas aún, cae en monto_vigente de la base
        vigente_real = (r.vigente_base or 0) + mod["neto"]

        # Protección: vigente no puede ser negativo
        vigente_real = max(0.0, vigente_real)

        var_nom  = ((vigente_real / r.original) - 1) * 100
        real_pesos = vigente_real / factor
        var_real = ((real_pesos / r.original) - 1) * 100

        resultado.append({
            "programa_id":             r.programa_id,
            "programa_desc":           r.programa_desc,
            "jurisdiccion":            r.jurisdiccion_desc,
            "inciso_id":               r.inciso_id,
            "inciso_desc":             r.inciso_desc,
            "monto_original":          round(r.original, 2),
            "monto_vigente_nominal":   round(vigente_real, 2),
            "monto_real_en_moneda_2023": round(real_pesos, 2),
            "variacion_nominal_pct":   round(var_nom, 2),
            "variacion_real_pct":      round(var_real, 2),
            "licuacion_pct":           round(var_nom - var_real, 2),
            "cantidad_modificaciones": mod["cant"],
            "estado_ajuste":           "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
        })

    resultado.sort(key=lambda x: x["variacion_real_pct"])
    return resultado[:top_n]


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
            "variacion_nominal_pct": round(((r.total_vigente / r.total_original) - 1) * 100, 2) if r.total_original else 0,
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

# TC fallback: valor hardcodeado actualizable si la API cambiaria falla.
# Actualizar manualmente si el TC se aleja mucho de este valor.
TC_USD_FALLBACK   = 1_150.0      # ARS/USD oficial aprox. (actualizar periódicamente)


def _label_mes(fecha_str: str) -> str:
    meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    try:
        p = fecha_str.split("-")
        return f"{meses[int(p[1])-1]} {p[0]}"
    except Exception:
        return fecha_str


async def _obtener_tc_usd(client: httpx.AsyncClient) -> tuple[float | None, bool]:
    """
    Intenta obtener el TC USD oficial desde la API BCRA cambiaria.
    Retorna (tc, es_fallback):
      - (valor, False) si la API respondió bien
      - (TC_USD_FALLBACK, True) si falló → usar valor hardcodeado
    La API BCRA cambiaria devuelve una lista de cotizaciones por fecha.
    Estructura: results = [{ "fecha": "...", "detalle": [{ "codigoMoneda": "USD", "tipoPase": 0.0, ... }] }]
    El TC oficial está en tipoPase o en el campo tipoCotizacion según el endpoint.
    Probamos el endpoint de divisas que devuelve venta del BNA.
    """
    hoy      = date.today().isoformat()
    hace_30d = (date.today() - timedelta(days=30)).isoformat()

    # Endpoint alternativo: /estadisticascambiarias/v1.0/Cotizaciones/USD
    # devuelve lista de { fecha, detalle: [{ tipoCotizacion, ... }] }
    urls_a_probar = [
        (
            f"{BCRA_CAMBIARIAS}/USD",
            {"fechadesde": hace_30d, "fechahasta": hoy, "limit": 10},
            # extractor: busca el primer tipoCotizacion > 0 en detalle
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
        # Fallback endpoint: variables monetarias variable 4 (TC referencia BCRA)
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

    # Ambos endpoints fallaron → usar fallback hardcodeado
    return TC_USD_FALLBACK, True


@app.get(
    "/api/v1/macro/base-monetaria",
    tags=["Macro"],
    summary="Base Monetaria — datos en vivo BCRA",
)
async def base_monetaria():
    """
    Consulta la API pública del BCRA (Variables Monetarias v4.0).
    Base Monetaria diaria desde dic-2023 a hoy + TC USD oficial.
    Sin autenticación requerida.
    """
    hoy = date.today().isoformat()

    async with httpx.AsyncClient(timeout=12, verify=False) as client:

        # 1. Serie diaria Base Monetaria desde asunción
        resp = await client.get(
            f"{BCRA_MONETARIAS}/{ID_BASE_MONETARIA}",
            params={"desde": FECHA_ASUNCION, "hasta": hoy, "limit": 3000},
        )
        if resp.status_code != 200:
            raise HTTPException(502, "Error al consultar API BCRA (base monetaria)")

        detalle     = resp.json()["results"][0]["detalle"]
        detalle_asc = list(reversed(detalle))   # la API devuelve desc → invertimos

        ultimo    = detalle_asc[-1]
        bm_actual = ultimo["valor"]
        fecha_ult = ultimo["fecha"]

        # 2. Tipo de cambio USD oficial — con fallback robusto
        tc_usd, tc_es_fallback = await _obtener_tc_usd(client)

    # 3. KPIs
    var_pct = round((bm_actual / BM_ASUNCION_MM - 1) * 100, 1)
    mult    = round(bm_actual / BM_ASUNCION_MM, 2)

    # BM en USD: siempre calculable ahora (tc_usd nunca es None)
    bm_usd_mm = round(bm_actual / tc_usd, 0)

    # 4. Serie mensual para gráfico (primer dato de cada mes)
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

    # Nota sobre el TC usado
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
