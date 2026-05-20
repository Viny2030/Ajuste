"""
router_social.py
================
Endpoint FastAPI que sirve los datos sociales al dashboard MAP.

Integrar en app/main.py:
    from app.routers.router_social import router as social_router
    app.include_router(social_router)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/social", tags=["social"])

DATA_FILE = (
    Path(__file__).resolve().parents[2]
    / "data" / "processed" / "social" / "indicadores_sociales.json"
)


def _cargar() -> dict:
    if not DATA_FILE.exists():
        raise HTTPException(
            status_code=503,
            detail="Datos sociales no disponibles aún. El scraper no ha corrido.",
        )
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@router.get("/indicadores")
def get_indicadores():
    """
    Devuelve todos los indicadores sociales con variaciones desde dic-2023.
    Usado por la sección 'Impacto Social' del dashboard.
    """
    return _cargar()


@router.get("/kpis")
def get_kpis():
    """
    Versión compacta para las tarjetas KPI del dashboard:
    valor actual, variación absoluta y % desde la base.
    """
    data = _cargar()
    ind  = data["indicadores"]

    return {
        "ultima_actualizacion": data["_meta"]["ultima_actualizacion"],
        "alertas_nuevos_datos": data["_meta"]["alertas_nuevos_datos"],
        "kpis": [
            {
                "id"          : "mortalidad_infantil",
                "titulo"      : "Mortalidad Infantil",
                "subtitulo"   : "Menores de 1 año / c/1.000 nacidos vivos",
                "valor_base"  : ind["mortalidad_infantil"]["linea_de_base"]["tasa_por_mil"],
                "valor_actual": ind["mortalidad_infantil"]["ultimo_dato"]["tasa_por_mil"],
                "var_absoluta": ind["mortalidad_infantil"]["variacion_desde_base"]["tasa"]["absoluta"],
                "var_pct"     : ind["mortalidad_infantil"]["variacion_desde_base"]["tasa"]["porcentual"],
                "direccion"   : "sube",
                "color"       : "rojo",
                "anio_base"   : ind["mortalidad_infantil"]["linea_de_base"]["anio"],
                "anio_actual" : ind["mortalidad_infantil"]["ultimo_dato"]["anio"],
                "fuente"      : ind["mortalidad_infantil"]["ultimo_dato"]["fuente"],
                "alertas"     : ind["mortalidad_infantil"]["alertas"],
            },
            {
                "id"          : "mortalidad_adultos_mayores",
                "titulo"      : "Mortalidad Adultos Mayores",
                "subtitulo"   : "Muertes adicionales en personas 65+",
                "valor_base"  : ind["mortalidad_adultos_mayores"]["linea_de_base"]["muertes_65_mas"],
                "valor_actual": ind["mortalidad_adultos_mayores"]["ultimo_dato"]["muertes_65_mas"],
                "var_absoluta": ind["mortalidad_adultos_mayores"]["variacion_desde_base"]["muertes_65_mas"]["absoluta"],
                "var_pct"     : ind["mortalidad_adultos_mayores"]["variacion_desde_base"]["muertes_65_mas"]["porcentual"],
                "direccion"   : "sube",
                "color"       : "rojo",
                "anio_base"   : ind["mortalidad_adultos_mayores"]["linea_de_base"]["anio"],
                "anio_actual" : ind["mortalidad_adultos_mayores"]["ultimo_dato"]["anio"],
                "fuente"      : ind["mortalidad_adultos_mayores"]["ultimo_dato"]["fuente"],
                "alertas"     : ind["mortalidad_adultos_mayores"]["alertas"],
            },
            {
                "id"          : "suicidios",
                "titulo"      : "Suicidios",
                "subtitulo"   : "Casos consumados anuales (SNIC)",
                "valor_base"  : ind["suicidios"]["linea_de_base"]["casos"],
                "valor_actual": ind["suicidios"]["ultimo_dato"]["casos"],
                "var_absoluta": ind["suicidios"]["variacion_desde_base"]["casos"]["absoluta"],
                "var_pct"     : ind["suicidios"]["variacion_desde_base"]["casos"]["porcentual"],
                "tasa_actual" : ind["suicidios"]["ultimo_dato"]["tasa_por_100k"],
                "tasa_base"   : ind["suicidios"]["linea_de_base"]["tasa_por_100k"],
                "var_pct_tasa": ind["suicidios"]["variacion_desde_base"]["tasa"]["porcentual"],
                "direccion"   : "sube",
                "color"       : "rojo",
                "anio_base"   : ind["suicidios"]["linea_de_base"]["anio"],
                "anio_actual" : ind["suicidios"]["ultimo_dato"]["anio"],
                "fuente"      : ind["suicidios"]["ultimo_dato"]["fuente"],
                "alertas"     : ind["suicidios"]["alertas"],
            },
        ],
    }


@router.get("/status")
def get_status():
    """Health-check: informa si los datos están actualizados y si hay alertas."""
    data   = _cargar()
    meta   = data["_meta"]
    alertas = meta.get("alertas_nuevos_datos", [])

    return {
        "ok"                  : True,
        "ultima_actualizacion": meta["ultima_actualizacion"],
        "hay_datos_nuevos_pendientes": len(alertas) > 0,
        "alertas"             : alertas,
        "cambios_ultima_corrida": meta.get("cambios_detectados", {}),
    }