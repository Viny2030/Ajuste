# app/core/deflactor.py
"""
Deflactor y tipo de cambio — fuente única para todos los endpoints.

Criterio (corrige el 10,53 fijo que se usaba antes):
  * El crédito presupuestario de un año está expresado en precios PROMEDIO de
    ese año. Para pasar pesos de 2026 a pesos de 2023 se usa la relación entre
    el nivel de precios promedio de cada año:

        factor(2023→2026) = promedio(nivel IPC 2026) / promedio(nivel IPC 2023)

    (para el año en curso, promedio de los meses publicados).
    Antes se usaba la inflación punta a punta dic-2022 → hoy (≈10,5), que
    compara el gasto de todo 2023 con precios de fin de período y exagera la
    caída real (p. ej. el total APN daba -50 % en vez de -35 %).
  * Dólares: tipo de cambio PROMEDIO de cada año (antes: $187 de enero 2023
    contra el tipo de cambio de hoy).

Fuentes, en orden:
  1. API oficial BCRA v4.0 (id 27 = inflación mensual %, id 5 = TC mayorista)
  2. data/seeds/macro_indices.csv (lo actualiza el workflow monthly_macro)
Si ambas fallan se usan valores de respaldo y la respuesta lo indica
("fuente": "respaldo").
"""
from __future__ import annotations

import csv
import logging
import os
import time
from collections import defaultdict
from datetime import date

logger = logging.getLogger("map.deflactor")

_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "seeds", "macro_indices.csv")
_TTL = 6 * 3600
_cache: dict = {"t": 0.0, "datos": None}

# Respaldo (calculado el 2026-10-10 con BCRA v4.0, meses ene-2023..ago-2026)
_RESPALDO_FACTOR = {2023: 1.0, 2024: 3.20, 2025: 4.54, 2026: 5.78}
_RESPALDO_TC = {2023: 309.9, 2024: 915.0, 2025: 1180.0, 2026: 1469.3}


def _desde_bcra():
    try:
        from app.core.engine import _fetch_bcra_v4
    except Exception:
        return None, None
    ipc = _fetch_bcra_v4(27, desde="2023-01-01")
    tc = _fetch_bcra_v4(5, desde="2023-01-01")
    ipc_m = {r["fecha"][:7]: float(r["valor"]) for r in ipc} if ipc else None
    tc_d = [(r["fecha"][:10], float(r["valor"])) for r in tc] if tc else None
    return ipc_m, tc_d


def _desde_csv():
    ipc_m, tc_d = {}, []
    try:
        with open(_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["fecha"] < "2023-01-01":
                    continue
                if row["indicador"] == "IPC_variacion_mensual":
                    ipc_m[row["fecha"][:7]] = float(row["valor"])
                elif row["indicador"] == "TC_oficial_venta":
                    tc_d.append((row["fecha"][:10], float(row["valor"])))
    except Exception as e:
        logger.warning(f"No se pudo leer {_CSV}: {e}")
    return (ipc_m or None), (sorted(tc_d) or None)


def _cargar():
    ahora = time.time()
    if _cache["datos"] and ahora - _cache["t"] < _TTL:
        return _cache["datos"]

    ipc_m, tc_d = _desde_bcra()
    fuente_ipc = fuente_tc = "BCRA v4.0"
    if not ipc_m or not tc_d:
        c_ipc, c_tc = _desde_csv()
        if not ipc_m:
            ipc_m, fuente_ipc = c_ipc, "data/seeds/macro_indices.csv"
        if not tc_d:
            tc_d, fuente_tc = c_tc, "data/seeds/macro_indices.csv"

    # Nivel de precios mensual (dic-2022 = 1)
    niveles = {}
    if ipc_m:
        nivel = 1.0
        for mes in sorted(ipc_m):
            nivel *= 1 + ipc_m[mes] / 100
            niveles[mes] = nivel

    tc_por_anio = defaultdict(list)
    for f, v in (tc_d or []):
        tc_por_anio[int(f[:4])].append(v)

    datos = {
        "niveles": niveles,
        "tc_por_anio": {a: sum(v) / len(v) for a, v in tc_por_anio.items() if v},
        "tc_actual": tc_d[-1][1] if tc_d else None,
        "ultimo_mes_ipc": max(niveles) if niveles else None,
        "fuente_ipc": fuente_ipc if niveles else "respaldo",
        "fuente_tc": fuente_tc if tc_d else "respaldo",
    }
    _cache.update(t=ahora, datos=datos)
    return datos


def _promedio_nivel(anio: int):
    v = [x for m, x in _cargar()["niveles"].items() if m.startswith(str(anio))]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def factor(anio_base: int = 2023, anio_comp: int = 2026) -> float:
    """Pesos de anio_comp → pesos de anio_base (relación de precios promedio)."""
    b, _ = _promedio_nivel(anio_base)
    c, _ = _promedio_nivel(anio_comp)
    if b and c:
        return c / b
    rb = _RESPALDO_FACTOR.get(anio_base)
    rc = _RESPALDO_FACTOR.get(anio_comp)
    if rb and rc:
        logger.warning("Deflactor de respaldo en uso")
        return rc / rb
    return 1.0


def tc_promedio(anio: int) -> float:
    """Tipo de cambio oficial promedio del año (año en curso: promedio a la fecha)."""
    v = _cargar()["tc_por_anio"].get(anio)
    return v or _RESPALDO_TC.get(anio) or 0.0


def tc_actual() -> float | None:
    return _cargar()["tc_actual"]


def metadata(anio_base: int = 2023, anio_comp: int = 2026) -> dict:
    d = _cargar()
    _, n = _promedio_nivel(anio_comp)
    return {
        "metodo": "precios promedio anuales (IPC INDEC vía BCRA); base = crédito vigente",
        "factor": round(factor(anio_base, anio_comp), 4),
        "anio_base": anio_base,
        "anio_comp": anio_comp,
        "meses_ipc_anio_comp": n,
        "ultimo_mes_ipc": d["ultimo_mes_ipc"],
        "tc_promedio_base": round(tc_promedio(anio_base), 2),
        "tc_promedio_comp": round(tc_promedio(anio_comp), 2),
        "fuente_ipc": d["fuente_ipc"],
        "fuente_tc": d["fuente_tc"],
    }
