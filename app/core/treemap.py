"""Árbol del presupuesto de un ejercicio: jurisdicción → programa → inciso.

Sólo describe un año por vez. No cruza años porque las jurisdicciones cambian
con cada reorganización del Estado (25 en 2023, 16 en 2026): comparar por código
mostraría como recorte o aumento lo que es un traslado de programas.
"""
import time

from sqlalchemy import text

TTL_SEGUNDOS = 600
_cache: dict[int, tuple[float, dict]] = {}


def _nombre_mayor(montos_por_nombre: dict[str, float], respaldo: str) -> str:
    """Un mismo código puede tener varias descripciones: gana la de mayor monto."""
    nombres = {n: m for n, m in montos_por_nombre.items() if n and n.strip()}
    if not nombres:
        return respaldo
    return max(sorted(nombres), key=lambda n: nombres[n]).strip()


def armar_arbol(filas) -> dict:
    """Recibe (jur_id, jur, prog_id, prog, inc_id, inc, monto) y arma el árbol.

    Las hojas sin monto positivo no se dibujan: un área no puede ser negativa.
    Se informan aparte en `omitidas` para que el total no parezca completo sin serlo.
    """
    jurs: dict = {}
    omitidas = 0
    for jur_id, jur, prog_id, prog, inc_id, inc, monto in filas:
        monto = float(monto or 0)
        if monto <= 0:
            omitidas += 1
            continue
        j = jurs.setdefault(str(jur_id), {"nombres": {}, "programas": {}})
        j["nombres"][jur] = j["nombres"].get(jur, 0) + monto
        p = j["programas"].setdefault(str(prog_id), {"nombres": {}, "incisos": {}})
        p["nombres"][prog] = p["nombres"].get(prog, 0) + monto
        i = p["incisos"].setdefault(str(inc_id), {"nombres": {}, "monto": 0.0})
        i["nombres"][inc] = i["nombres"].get(inc, 0) + monto
        i["monto"] += monto

    salida = []
    for jur_id, j in jurs.items():
        programas = []
        for prog_id, p in j["programas"].items():
            incisos = [
                {
                    "id": inc_id,
                    "nombre": _nombre_mayor(i["nombres"], f"Inciso {inc_id}"),
                    "monto": round(i["monto"], 2),
                }
                for inc_id, i in p["incisos"].items()
            ]
            incisos.sort(key=lambda x: -x["monto"])
            programas.append({
                "id": prog_id,
                "nombre": _nombre_mayor(p["nombres"], f"Programa {prog_id}"),
                "monto": round(sum(x["monto"] for x in incisos), 2),
                "incisos": incisos,
            })
        programas.sort(key=lambda x: -x["monto"])
        salida.append({
            "id": jur_id,
            "nombre": _nombre_mayor(j["nombres"], f"Jurisdicción {jur_id}"),
            "monto": round(sum(x["monto"] for x in programas), 2),
            "programas": programas,
        })
    salida.sort(key=lambda x: -x["monto"])
    return {
        "total": round(sum(x["monto"] for x in salida), 2),
        "omitidas": omitidas,
        "jurisdicciones": salida,
    }


def anios_disponibles(db) -> list[int]:
    filas = db.execute(text(
        "SELECT DISTINCT ejercicio FROM presupuesto_base "
        "WHERE ejercicio IS NOT NULL ORDER BY ejercicio"
    )).fetchall()
    return [int(f[0]) for f in filas]


def datos(db, anio: int | None = None) -> dict | None:
    """Árbol de `anio` (o del último cargado) con caché de 10 minutos; None si no está."""
    clave = anio if anio is not None else 0
    guardado = _cache.get(clave)
    if guardado and time.monotonic() - guardado[0] < TTL_SEGUNDOS:
        return guardado[1]
    disponibles = anios_disponibles(db)
    if anio is None and disponibles:
        anio = disponibles[-1]
    if anio not in disponibles:
        return None
    filas = db.execute(text("""
        SELECT jurisdiccion_id, jurisdiccion_desc,
               programa_id, programa_desc,
               inciso_id, inciso_desc,
               SUM(monto_vigente)
        FROM presupuesto_base
        WHERE ejercicio = :anio
        GROUP BY jurisdiccion_id, jurisdiccion_desc,
                 programa_id, programa_desc,
                 inciso_id, inciso_desc
    """), {"anio": anio}).fetchall()
    resultado = {
        "anio": anio,
        "disponibles": disponibles,
        "medida": "crédito vigente, en pesos corrientes de cada año",
        **armar_arbol(filas),
    }
    _cache[clave] = (time.monotonic(), resultado)
    return resultado
