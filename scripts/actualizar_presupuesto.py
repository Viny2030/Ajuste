"""
actualizar_presupuesto.py
=========================
Reemplaza en presupuesto_base las filas de un ejercicio con el dataset
"crédito anual" vigente de Presupuesto Abierto (MECON), que se publica
actualizado varias veces por semana.

Por qué existe: el workflow diario sólo cargaba 2026 "si no existía" y
scripts/load_2026_to_db.py escribe siempre en sqlite:///sql_app.db con el
credito2026.zip guardado en el repo. Resultado: el 2026 de producción quedó
congelado (vigente $145,6 billones contra $152,5 billones publicados al 07/10/2026).

Usa la misma DATABASE_URL que la app (app/database/session.py) y hace el
borrado + inserción en UNA transacción: si algo falla no se pierde el año.

Uso:
  python -m scripts.actualizar_presupuesto --anio 2026
  python -m scripts.actualizar_presupuesto --anio 2025 --zip-local credito-anual-2025.zip
  python -m scripts.actualizar_presupuesto --anio 2026 --dry-run
"""
import argparse
import csv
import io
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.database.session import engine, DATABASE_URL  # noqa: E402

URL = "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/{anio}/credito-anual-{anio}.zip"
COLS = [
    "jurisdiccion_id", "jurisdiccion_desc", "entidad_id", "entidad_desc",
    "programa_id", "programa_desc", "subprograma_id", "proyecto_id",
    "actividad_id", "obra_id", "inciso_id", "inciso_desc", "principal_id",
    "principal_desc", "parcial_id", "parcial_desc", "subparcial_id",
    "subparcial_desc", "fuente_financiamiento_id", "fuente_financiamiento_desc",
    "ubicacion_geografica_id",
]


def _monto(s):
    # El CSV viene en MILLONES de pesos con coma decimal → pesos
    try:
        return float(str(s or "0").replace(",", ".")) * 1_000_000
    except ValueError:
        return 0.0


def leer_csv(anio, zip_local=None):
    if zip_local:
        data = Path(zip_local).read_bytes()
    else:
        req = Request(URL.format(anio=anio), headers={"User-Agent": "Mozilla/5.0 (MAP)"})
        with urlopen(req, timeout=180) as r:
            data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        nombre = next(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(nombre) as f:
            filas = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    corte = filas[0].get("ultima_actualizacion_fecha", "") if filas else ""
    out = []
    for r in filas:
        if str(r.get("ejercicio_presupuestario", anio)).strip() != str(anio):
            continue
        d = {c: (r.get(c) or None) for c in COLS}
        d["ejercicio"] = anio
        d["monto_original"] = _monto(r.get("credito_presupuestado"))
        d["monto_vigente"] = _monto(r.get("credito_vigente"))
        out.append(d)
    return out, corte


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anio", type=int, required=True)
    ap.add_argument("--zip-local", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    filas, corte = leer_csv(args.anio, args.zip_local)
    vig = sum(f["monto_vigente"] for f in filas)
    print(f"{args.anio}: {len(filas):,} filas · vigente ${vig/1e12:,.2f} billones · {corte}")
    if not filas:
        print("Sin filas: no se toca la base.")
        return 1
    if args.dry_run:
        return 0

    destino = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    print(f"Base destino: {destino}")
    cols = ["ejercicio"] + COLS + ["monto_original", "monto_vigente"]
    ins = text(f"INSERT INTO presupuesto_base ({', '.join(cols)}) "
               f"VALUES ({', '.join(':' + c for c in cols)})")
    with engine.begin() as conn:  # una sola transacción
        antes = conn.execute(text("SELECT COUNT(1), COALESCE(SUM(monto_vigente),0) "
                                  "FROM presupuesto_base WHERE ejercicio = :a"), {"a": args.anio}).fetchone()
        conn.execute(text("DELETE FROM presupuesto_base WHERE ejercicio = :a"), {"a": args.anio})
        for i in range(0, len(filas), 5000):
            conn.execute(ins, filas[i:i + 5000])
    print(f"Antes: {antes[0]:,} filas · ${float(antes[1])/1e12:,.2f} billones → "
          f"ahora: {len(filas):,} filas · ${vig/1e12:,.2f} billones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
