"""
ingest_presupuesto_2026.py
==========================
Baja e integra el presupuesto base 2026 al pipeline de análisis.

Fuente: https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2026/
Unidad del CSV: MILLONES de ARS (igual que 2024)
Actualización: el archivo se regenera diariamente con ejecución acumulada.

Uso:
    python ingest_presupuesto_2026.py               # baja y procesa todo
    python ingest_presupuesto_2026.py --solo-jgm    # filtra solo jurisdicción 25
    python ingest_presupuesto_2026.py --mensual      # baja también el crédito mensual
"""

import argparse
import csv
import io
import logging
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

BASE_URL = "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2026"
URL_ANUAL   = f"{BASE_URL}/credito-anual-2026.zip"
URL_MENSUAL = f"{BASE_URL}/credito-mensual-2026.zip"

# Unidad de los créditos en el CSV (igual que 2024)
UNIDAD = "millones_ars"

# Jurisdicción JGM en 2026
JURISDICCION_JGM = "25"
JURISDICCION_DESC_JGM = "Jefatura de Gabinete de Ministros"

# Directorio de salida (ajustá según tu estructura de proyecto)
OUTPUT_DIR = Path("data/presupuesto_base")


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar_zip(url: str) -> bytes:
    """Descarga un ZIP y devuelve sus bytes."""
    log.info(f"Descargando {url} ...")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=60) as r:
            data = r.read()
        log.info(f"  → {len(data)/1e6:.1f} MB descargados")
        return data
    except URLError as e:
        log.error(f"Error al descargar {url}: {e}")
        raise


def leer_csv_de_zip(data: bytes) -> list[dict]:
    """Abre un ZIP en memoria y lee el único CSV que contiene."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        nombre = zf.namelist()[0]
        log.info(f"  → Leyendo {nombre} ...")
        with zf.open(nombre) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            rows = list(reader)
    log.info(f"  → {len(rows):,} filas leídas")
    return rows


# ── Parsing numérico ──────────────────────────────────────────────────────────

def parse_monto(s: str) -> float:
    """
    Convierte el formato argentino del CSV a float.
    Ejemplo: '112314,626039' → 112314.626039
    No hay separador de miles — solo coma decimal.
    """
    if not s:
        return 0.0
    return float(s.replace(",", "."))


# ── Procesamiento ─────────────────────────────────────────────────────────────

COLS_CREDITO = [
    "credito_presupuestado",
    "credito_vigente",
    "credito_comprometido",
    "credito_devengado",
    "credito_pagado",
]

COLS_CLASIFICACION = [
    "ejercicio_presupuestario",
    "jurisdiccion_id",
    "jurisdiccion_desc",
    "subjurisdiccion_id",
    "subjurisdiccion_desc",
    "entidad_id",
    "entidad_desc",
    "servicio_id",
    "servicio_desc",
    "programa_id",
    "programa_desc",
    "subprograma_id",
    "subprograma_desc",
    "actividad_id",
    "actividad_desc",
    "inciso_id",
    "inciso_desc",
    "principal_id",
    "principal_desc",
    "fuente_financiamiento_id",
    "fuente_financiamiento_desc",
    "finalidad_id",
    "finalidad_desc",
    "funcion_id",
    "funcion_desc",
    "ultima_actualizacion_fecha",
]


def procesar_filas(rows: list[dict], solo_jgm: bool = False) -> list[dict]:
    """
    Convierte las filas crudas del CSV en registros normalizados.
    Agrega campo `unidad` = 'millones_ars'.
    """
    resultado = []
    for row in rows:
        if solo_jgm and row.get("jurisdiccion_id") != JURISDICCION_JGM:
            continue
        registro = {col: row.get(col, "") for col in COLS_CLASIFICACION}
        for col in COLS_CREDITO:
            registro[col] = parse_monto(row.get(col, ""))
        registro["unidad"] = UNIDAD
        resultado.append(registro)
    return resultado


def resumen_por_jurisdiccion(rows: list[dict]) -> dict:
    """Totales por jurisdicción — útil para validación rápida."""
    totales = defaultdict(lambda: defaultdict(float))
    for r in rows:
        jid = r["jurisdiccion_id"]
        jdesc = r["jurisdiccion_desc"]
        key = f"{jid} | {jdesc}"
        for col in COLS_CREDITO:
            totales[key][col] += r.get(col, 0.0)
    return dict(totales)


def resumen_jgm_por_inciso(rows: list[dict]) -> dict:
    """Totales JGM por inciso — para integrar a analisis.py."""
    totales = defaultdict(float)
    for r in rows:
        if r["jurisdiccion_id"] == JURISDICCION_JGM:
            totales[r["inciso_desc"]] += r.get("credito_presupuestado", 0.0)
    return dict(totales)


def resumen_jgm_por_programa(rows: list[dict]) -> dict:
    """Totales JGM por programa — para Q1 y Q2."""
    totales = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if r["jurisdiccion_id"] == JURISDICCION_JGM:
            key = f"{r['programa_id']:3s} | {r['programa_desc']}"
            for col in COLS_CREDITO:
                totales[key][col] += r.get(col, 0.0)
    return dict(totales)


# ── Guardado CSV ──────────────────────────────────────────────────────────────

def guardar_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        log.warning("Sin filas para guardar.")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info(f"  → Guardado: {path} ({len(rows):,} filas)")


# ── Impresión de resúmenes ────────────────────────────────────────────────────

def imprimir_resumen_jgm(rows: list[dict]) -> None:
    jgm = [r for r in rows if r["jurisdiccion_id"] == JURISDICCION_JGM]
    if not jgm:
        log.warning("No hay filas JGM en los datos procesados.")
        return

    pres = sum(r["credito_presupuestado"] for r in jgm)
    vig  = sum(r["credito_vigente"] for r in jgm)
    dev  = sum(r["credito_devengado"] for r in jgm)
    ejec = (dev / vig * 100) if vig > 0 else 0.0

    print("\n" + "="*60)
    print(f"  PRESUPUESTO 2026 — JGM (Jurisdicción {JURISDICCION_JGM})")
    print("="*60)
    print(f"  Unidad: {UNIDAD}")
    print(f"  Presupuestado: {pres:>15,.1f}")
    print(f"  Vigente:       {vig:>15,.1f}")
    print(f"  Devengado:     {dev:>15,.1f}")
    print(f"  Ejecución:     {ejec:>14.1f}%")
    print()

    inciso = defaultdict(float)
    for r in jgm:
        inciso[r["inciso_desc"]] += r["credito_presupuestado"]
    print("  Por inciso (millones ARS):")
    for k, v in sorted(inciso.items(), key=lambda x: -x[1]):
        print(f"    {k:<40s} {v:>12,.1f}")

    prog = defaultdict(float)
    for r in jgm:
        prog[f"{r['programa_id']:3s} {r['programa_desc'][:45]}"] += r["credito_presupuestado"]
    print()
    print("  Top 10 programas (millones ARS):")
    for k, v in sorted(prog.items(), key=lambda x: -x[1])[:10]:
        print(f"    {k:<50s} {v:>12,.1f}")
    print("="*60)


def imprimir_resumen_apn(rows: list[dict]) -> None:
    pres_total = sum(r["credito_presupuestado"] for r in rows)
    vig_total  = sum(r["credito_vigente"] for r in rows)
    dev_total  = sum(r["credito_devengado"] for r in rows)
    ejec = (dev_total / vig_total * 100) if vig_total > 0 else 0.0

    print("\n" + "="*60)
    print("  PRESUPUESTO 2026 — APN TOTAL")
    print("="*60)
    print(f"  Presupuestado: {pres_total:>15,.1f}  millones ARS")
    print(f"               ({pres_total/1e6:,.2f}  billones ARS)")
    print(f"  Vigente:       {vig_total:>15,.1f}")
    print(f"  Devengado:     {dev_total:>15,.1f}")
    print(f"  Ejecución:     {ejec:>14.1f}%")
    print()
    by_jur = defaultdict(float)
    for r in rows:
        by_jur[f"{r['jurisdiccion_id']:3s} | {r['jurisdiccion_desc']}"] += r["credito_presupuestado"]
    print("  Por jurisdicción (top 10):")
    for k, v in sorted(by_jur.items(), key=lambda x: -x[1])[:10]:
        pct = v / pres_total * 100 if pres_total else 0
        print(f"    {k:<50s} {v:>12,.1f}  ({pct:.1f}%)")
    print("="*60)


# ── Integración con analisis.py ───────────────────────────────────────────────

def exportar_para_analisis(rows: list[dict], output_dir: Path) -> None:
    """
    Genera los archivos que consume analisis.py:
      - presupuesto_2026_jgm_completo.csv  → reemplaza la query de base
      - presupuesto_2026_por_inciso.csv    → para Q3 (presupuesto original vs modificado)
      - presupuesto_2026_por_programa.csv  → para Q1 y Q2
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. JGM completo
    jgm = [r for r in rows if r["jurisdiccion_id"] == JURISDICCION_JGM]
    guardar_csv(jgm, output_dir / "presupuesto_2026_jgm_completo.csv")

    # 2. Por inciso (para Q3)
    inciso_rows = []
    inciso_totals = defaultdict(lambda: defaultdict(float))
    for r in jgm:
        key = (r["inciso_id"], r["inciso_desc"])
        for col in COLS_CREDITO:
            inciso_totals[key][col] += r.get(col, 0.0)
    for (iid, idesc), totals in sorted(inciso_totals.items()):
        row_out = {
            "ejercicio": 2026,
            "jurisdiccion": JURISDICCION_DESC_JGM,
            "inciso_id": iid,
            "inciso_desc": idesc,
            "unidad": UNIDAD,
        }
        row_out.update(totals)
        inciso_rows.append(row_out)
    guardar_csv(inciso_rows, output_dir / "presupuesto_2026_por_inciso.csv")

    # 3. Por programa (para Q1/Q2)
    prog_rows = []
    prog_totals = defaultdict(lambda: defaultdict(float))
    meta_prog = {}
    for r in jgm:
        key = (r["programa_id"], r["programa_desc"])
        meta_prog[key] = {
            "servicio_id": r["servicio_id"],
            "servicio_desc": r["servicio_desc"],
        }
        for col in COLS_CREDITO:
            prog_totals[key][col] += r.get(col, 0.0)
    for (pid, pdesc), totals in sorted(prog_totals.items()):
        row_out = {
            "ejercicio": 2026,
            "jurisdiccion": JURISDICCION_DESC_JGM,
            "programa_id": pid,
            "programa_desc": pdesc,
            "unidad": UNIDAD,
            **meta_prog[(pid, pdesc)],
        }
        row_out.update(totals)
        prog_rows.append(row_out)
    guardar_csv(prog_rows, output_dir / "presupuesto_2026_por_programa.csv")

    log.info(f"Archivos para analisis.py guardados en {output_dir}/")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ingesta del presupuesto 2026")
    parser.add_argument("--solo-jgm", action="store_true",
                        help="Guardar solo filas de JGM (jurisdicción 25)")
    parser.add_argument("--mensual", action="store_true",
                        help="Bajar también el crédito mensual")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR,
                        help=f"Directorio de salida (default: {OUTPUT_DIR})")
    parser.add_argument("--sin-descarga", action="store_true",
                        help="Usar archivo local credito2026.zip si existe")
    args = parser.parse_args()

    # ── Descargar o leer local ──
    zip_local = Path("credito2026.zip")
    if args.sin_descarga and zip_local.exists():
        log.info(f"Usando archivo local: {zip_local}")
        data_anual = zip_local.read_bytes()
    else:
        data_anual = descargar_zip(URL_ANUAL)
        zip_local.write_bytes(data_anual)
        log.info(f"ZIP guardado localmente en {zip_local}")

    # ── Procesar anual ──
    rows_raw = leer_csv_de_zip(data_anual)
    rows = procesar_filas(rows_raw, solo_jgm=False)

    imprimir_resumen_apn(rows)
    imprimir_resumen_jgm(rows)

    # ── Guardar CSVs ──
    exportar_para_analisis(rows, args.output_dir)

    # Archivo completo (o solo JGM)
    if args.solo_jgm:
        jgm_rows = [r for r in rows if r["jurisdiccion_id"] == JURISDICCION_JGM]
        guardar_csv(jgm_rows, args.output_dir / "credito_anual_2026_jgm.csv")
    else:
        guardar_csv(rows, args.output_dir / "credito_anual_2026_completo.csv")

    # ── Opcional: mensual ──
    if args.mensual:
        log.info("Descargando crédito mensual 2026 ...")
        data_mensual = descargar_zip(URL_MENSUAL)
        rows_mensual_raw = leer_csv_de_zip(data_mensual)
        rows_mensual = procesar_filas(rows_mensual_raw, solo_jgm=args.solo_jgm)
        guardar_csv(rows_mensual, args.output_dir / "credito_mensual_2026.csv")
        log.info(f"Mensual: {len(rows_mensual):,} filas guardadas")

    log.info("✓ Ingesta 2026 completada")


if __name__ == "__main__":
    main()