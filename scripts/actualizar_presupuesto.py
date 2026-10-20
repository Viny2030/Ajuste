"""
actualizar_presupuesto.py
=========================
Reemplaza en presupuesto_base las filas de un ejercicio con el dataset
"crédito anual" vigente de Presupuesto Abierto (MECON), que se publica
actualizado varias veces por semana.

Usa la misma DATABASE_URL que la app (app/database/session.py) y hace el
borrado + inserción en UNA sola transacción PostgreSQL usando COPY FROM STDIN.

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
    # _monto() multiplica ×1_000_000 porque MECON publica en millones de pesos.
    # Los ZIP de MECON no traen columna "unidad" (la agrega
    # scripts/ingest_presupuesto_2026.py en sus CSV); si viene, se verifica.
    # El control que sí cubre a MECON es control_magnitud(), en main().
    unidades = {(r.get("unidad") or "").strip().lower() for r in filas} - {""}
    if unidades and unidades != {"millones_ars"}:
        raise ValueError(f"Unidad inesperada en el dataset: {sorted(unidades)} (se esperaba millones_ars)")
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


def bulk_copy_postgres(conn, cols, filas):
    """
    Transfiere en masa vía stream de texto en memoria directo a Postgres.
    Tarda ~2 a 4 segundos para 85.000 filas.

    Usa COPY en FORMAT csv, que entiende las comillas que escribe csv.writer.
    (Con FORMAT text, una descripción con comillas, tabulador o barra
    invertida se guardaba alterada o rompía el COPY.) En formato csv un campo
    vacío sin comillas es NULL: csv.writer escribe None como vacío sin comillas.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for f in filas:
        writer.writerow([f[c] for c in cols])
    buffer.seek(0)

    # Obtenemos el cursor crudo del driver psycopg2 subyacente
    raw_cursor = conn.connection.cursor()
    cols_joined = ", ".join(cols)
    sql = f"COPY presupuesto_base ({cols_joined}) FROM STDIN WITH (FORMAT csv)"
    raw_cursor.copy_expert(sql, buffer)
    raw_cursor.close()


def insertar(conn, cols, filas):
    """COPY en Postgres; INSERT por lotes en cualquier otra base (p. ej. el
    SQLite local, donde COPY no existe)."""
    if conn.dialect.name == "postgresql":
        bulk_copy_postgres(conn, cols, filas)
        return
    ins = text(f"INSERT INTO presupuesto_base ({', '.join(cols)}) "
               f"VALUES ({', '.join(':' + c for c in cols)})")
    for i in range(0, len(filas), 5000):
        conn.execute(ins, filas[i:i + 5000])


# Orden de magnitud razonable del crédito vigente total de un ejercicio, en
# pesos: 2023 = $40 billones, 2026 = $152 billones. Si MECON pasara a publicar
# en miles, el total daría ~$0,15 billones y la carga se corta. No se puede
# saltear con --forzar: es un error de unidades, no un cambio real.
MAGNITUD_MIN = 1e13   # $10 billones
MAGNITUD_MAX = 1e16   # $10.000 billones


def control_magnitud(vig_total: float) -> str | None:
    """Devuelve el motivo para abortar si el total no tiene el orden esperado."""
    if not (MAGNITUD_MIN <= vig_total <= MAGNITUD_MAX):
        return (f"vigente total ${vig_total / 1e12:,.2f} billones, fuera del rango esperado "
                f"(${MAGNITUD_MIN / 1e12:,.0f}–${MAGNITUD_MAX / 1e12:,.0f} billones): "
                f"¿cambió la unidad del dataset?")
    return None


# Una caída grande de un día para el otro no es un recorte: es un ZIP de MECON
# incompleto o un cambio de formato. Se aborta antes del DELETE.
MAX_CAIDA_VIGENTE = 0.10   # 10 % del crédito vigente total
MAX_CAIDA_FILAS = 0.20     # 20 % de las filas


def control_cordura(filas_antes: int, vig_antes: float, filas_nuevas: int, vig_nuevo: float) -> str | None:
    """Devuelve el motivo para abortar, o None si la carga es razonable."""
    if filas_antes <= 0:
        return None
    if vig_antes > 0 and vig_nuevo < vig_antes * (1 - MAX_CAIDA_VIGENTE):
        return (f"el vigente cae {100 * (1 - vig_nuevo / vig_antes):.1f} % "
                f"(${vig_antes / 1e12:,.2f} → ${vig_nuevo / 1e12:,.2f} billones)")
    if filas_nuevas < filas_antes * (1 - MAX_CAIDA_FILAS):
        return f"las filas caen de {filas_antes:,} a {filas_nuevas:,}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anio", type=int, required=True)
    ap.add_argument("--zip-local", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--forzar", action="store_true",
                    help="Cargar aunque el control de cordura detecte una caída grande")
    args = ap.parse_args()

    filas, corte = leer_csv(args.anio, args.zip_local)
    vig = sum(f["monto_vigente"] for f in filas)
    print(f"{args.anio}: {len(filas):,} filas · vigente ${vig/1e12:,.2f} billones · {corte}")
    if not filas:
        print("Sin filas: no se toca la base.")
        return 1
    error_unidad = control_magnitud(vig)
    if error_unidad:
        print(f"::error::Carga abortada, no se tocó la base: {error_unidad}")
        return 1
    if args.dry_run:
        return 0

    destino = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    print(f"Base destino: {destino}")
    cols = ["ejercicio"] + COLS + ["monto_original", "monto_vigente"]

    # Abre la conexión únicamente al momento de persistir
    # engine.begin() maneja la transacción: si COPY falla, hace ROLLBACK del DELETE
    with engine.begin() as conn:
        antes = conn.execute(
            text("SELECT COUNT(1), COALESCE(SUM(monto_vigente),0) FROM presupuesto_base WHERE ejercicio = :a"),
            {"a": args.anio}
        ).fetchone()

        motivo = control_cordura(int(antes[0]), float(antes[1]), len(filas), vig)
        if motivo and not args.forzar:
            print(f"::error::Carga abortada, no se tocó la base: {motivo}. "
                  f"Si el cambio es real, correr con --forzar.")
            return 1
        if motivo:
            print(f"⚠️  {motivo} — se carga igual (--forzar)")

        print(f"Borrando registros previos de {args.anio}...")
        conn.execute(text("DELETE FROM presupuesto_base WHERE ejercicio = :a"), {"a": args.anio})

        print(f"Cargando {len(filas):,} registros ({conn.dialect.name})...")
        insertar(conn, cols, filas)

    print(f"Antes: {antes[0]:,} filas · ${float(antes[1])/1e12:,.2f} billones → "
          f"ahora: {len(filas):,} filas · ${vig/1e12:,.2f} billones")
    return 0


if __name__ == "__main__":
    sys.exit(main())
