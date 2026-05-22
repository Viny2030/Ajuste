"""
load_2026_to_db.py
==================
Inserta el presupuesto 2026 en la tabla presupuesto_base de sql_app.db.

Mapeo CSV -> tabla:
  credito_presupuestado -> monto_original
  credito_vigente       -> monto_vigente
  (resto de columnas: match directo)

Uso:
  python load_2026_to_db.py              # inserta (falla si ya existe 2026)
  python load_2026_to_db.py --reemplazar # borra 2026 y reinserta
  python load_2026_to_db.py --dry-run    # muestra que insertaria sin tocar la DB
"""

import argparse
import csv
import io
import zipfile
from pathlib import Path

from sqlalchemy import create_engine, text

DATABASE_URL = "sqlite:///sql_app.db"
ZIP_PATH     = Path("credito2026.zip")
URL_ANUAL    = "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2026/credito-anual-2026.zip"


def parse_monto(s: str) -> float:
    if not s:
        return 0.0
    return float(s.replace(",", "."))


def cargar_filas_csv() -> list[dict]:
    if not ZIP_PATH.exists():
        print(f"No se encontro {ZIP_PATH}, descargando...")
        from urllib.request import urlopen, Request
        req = Request(URL_ANUAL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=60) as r:
            data = r.read()
        ZIP_PATH.write_bytes(data)
        print(f"  -> Descargado ({len(data)/1e6:.1f} MB)")

    with zipfile.ZipFile(ZIP_PATH) as zf:
        nombre = zf.namelist()[0]
        with zf.open(nombre) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            rows = list(reader)
    print(f"  -> {len(rows):,} filas leidas del CSV")
    return rows


def csv_a_db_row(r: dict) -> dict:
    """Convierte una fila del CSV 2026 al formato de presupuesto_base."""
    return {
        "ejercicio":                int(r["ejercicio_presupuestario"]),
        "jurisdiccion_id":          r["jurisdiccion_id"],
        "jurisdiccion_desc":        r["jurisdiccion_desc"],
        "entidad_id":               r["entidad_id"],
        "entidad_desc":             r["entidad_desc"],
        "programa_id":              r["programa_id"],
        "programa_desc":            r["programa_desc"],
        "subprograma_id":           r["subprograma_id"],
        "proyecto_id":              r["proyecto_id"],
        "actividad_id":             r["actividad_id"],
        "obra_id":                  r["obra_id"],
        "inciso_id":                r["inciso_id"],
        "inciso_desc":              r["inciso_desc"],
        "principal_id":             r["principal_id"],
        "principal_desc":           r["principal_desc"],
        "parcial_id":               r["parcial_id"],
        "parcial_desc":             r["parcial_desc"],
        "subparcial_id":            r["subparcial_id"],
        "subparcial_desc":          r["subparcial_desc"],
        "fuente_financiamiento_id": r["fuente_financiamiento_id"],
        "fuente_financiamiento_desc": r["fuente_financiamiento_desc"],
        "ubicacion_geografica_id":  r["ubicacion_geografica_id"],
        # CSV 2026 viene en millones de pesos — convertir a pesos
        "monto_original":           parse_monto(r["credito_presupuestado"]) * 1000000,
        "monto_vigente":            parse_monto(r["credito_vigente"]) * 1000000,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reemplazar", action="store_true",
                        help="Borrar filas 2026 existentes antes de insertar")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo mostrar estadisticas sin escribir en la DB")
    args = parser.parse_args()

    print("Cargando CSV 2026...")
    rows_csv = cargar_filas_csv()
    rows_db  = [csv_a_db_row(r) for r in rows_csv]

    jgm = [r for r in rows_db if r["jurisdiccion_id"] == "25"]
    print(f"  -> Filas totales APN:  {len(rows_db):,}")
    print(f"  -> Filas JGM (jur 25): {len(jgm):,}")
    pres_jgm = sum(r["monto_original"] for r in jgm)
    print(f"  -> Presupuestado JGM:  {pres_jgm/1e6:,.1f} millones ARS")

    if args.dry_run:
        print("\n[DRY RUN] No se escribio nada en la DB.")
        return

    engine = create_engine(DATABASE_URL)
    with engine.begin() as conn:
        n_existentes = conn.execute(
            text("SELECT COUNT(1) FROM presupuesto_base WHERE ejercicio = 2026")
        ).scalar()

        if n_existentes > 0 and not args.reemplazar:
            print(f"\nERROR: Ya existen {n_existentes:,} filas de 2026 en la DB.")
            print("Usa --reemplazar para borrarlas y reinsertar.")
            return

        if n_existentes > 0 and args.reemplazar:
            conn.execute(text("DELETE FROM presupuesto_base WHERE ejercicio = 2026"))
            print(f"  -> Borradas {n_existentes:,} filas de 2026 previas")

        INSERT_SQL = text("""
            INSERT INTO presupuesto_base (
                ejercicio, jurisdiccion_id, jurisdiccion_desc,
                entidad_id, entidad_desc, programa_id, programa_desc,
                subprograma_id, proyecto_id, actividad_id, obra_id,
                inciso_id, inciso_desc, principal_id, principal_desc,
                parcial_id, parcial_desc, subparcial_id, subparcial_desc,
                fuente_financiamiento_id, fuente_financiamiento_desc,
                ubicacion_geografica_id, monto_original, monto_vigente
            ) VALUES (
                :ejercicio, :jurisdiccion_id, :jurisdiccion_desc,
                :entidad_id, :entidad_desc, :programa_id, :programa_desc,
                :subprograma_id, :proyecto_id, :actividad_id, :obra_id,
                :inciso_id, :inciso_desc, :principal_id, :principal_desc,
                :parcial_id, :parcial_desc, :subparcial_id, :subparcial_desc,
                :fuente_financiamiento_id, :fuente_financiamiento_desc,
                :ubicacion_geografica_id, :monto_original, :monto_vigente
            )
        """)

        BATCH = 5000
        total = len(rows_db)
        for i in range(0, total, BATCH):
            batch = rows_db[i:i+BATCH]
            conn.execute(INSERT_SQL, batch)
            pct = min(i + BATCH, total)
            print(f"  -> Insertadas {pct:,}/{total:,} filas...", end="\r")
        print()

    with engine.connect() as conn:
        n_final = conn.execute(
            text("SELECT COUNT(1) FROM presupuesto_base WHERE ejercicio = 2026")
        ).scalar()
        totales = conn.execute(text("""
            SELECT ejercicio, COUNT(1) as n, SUM(monto_vigente) as total
            FROM presupuesto_base
            GROUP BY ejercicio ORDER BY ejercicio
        """)).fetchall()

    print(f"\n OK Insertadas {n_final:,} filas de 2026 en presupuesto_base")
    print("\nEstado final de la tabla:")
    for t in totales:
        print(f"  {t[0]}: {t[1]:,} filas  |  SUM monto_vigente: {t[2]:,.0f}")


if __name__ == "__main__":
    main()