"""
scripts/seed_macro_indices.py
─────────────────────────────
Pobla macro_indices con:
  - IPC mensual (variación %) desde argentinadatos.com
  - TC oficial diario (venta) desde argentinadatos.com

Usa la misma conexión que la app (app/database/session.py): si la variable
de entorno DATABASE_URL está definida (como en Railway) escribe directo en
esa Postgres; si no, cae a sqlite local (./sql_app.db), igual que la app.

Nota (2026-07): antes este script escribía SIEMPRE en un sqlite3 local con
conexión propia, sin pasar por SQLAlchemy ni por DATABASE_URL — así que en
Railway nunca llegaba a tocar la Postgres real, aunque el script "corriera
bien". Si volvés a ver esto roto, lo primero es confirmar con
`echo $DATABASE_URL` (o `railway variables`) que la variable esté seteada
en el entorno donde corrés el script — sin eso, cae a sqlite silenciosamente
igual que antes.

Además exporta data/seeds/macro_indices.csv para versionado en git.

Uso:
    python -m scripts.seed_macro_indices                 # usa DATABASE_URL del entorno
    DATABASE_URL="postgresql://..." python -m scripts.seed_macro_indices   # explícito
"""

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.session import SessionLocal, engine, DATABASE_URL
from app.database.models import Base, MacroIndice

SEEDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "seeds")
CSV_PATH = os.path.join(SEEDS_DIR, "macro_indices.csv")
FUENTE = "argentinadatos.com"


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _parse_fecha(s: str) -> datetime:
    """Las APIs devuelven 'YYYY-MM-DD'; MacroIndice.fecha es DateTime."""
    return datetime.strptime(s, "%Y-%m-%d")


def seed():
    print(f"Conectando a: {DATABASE_URL[:45]}...")
    Base.metadata.create_all(bind=engine)  # asegura que la tabla exista

    db = SessionLocal()
    try:
        # Limpiar registros anteriores de esta fuente
        borrados = db.query(MacroIndice).filter(MacroIndice.fuente == FUENTE).delete()
        db.commit()
        print(f"  🗑️  {borrados} registros anteriores de '{FUENTE}' eliminados")

        # ── IPC mensual ──────────────────────────────────────────────────
        print("Descargando IPC...")
        ipc = fetch("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
        ipc_rows = [
            MacroIndice(fecha=_parse_fecha(row["fecha"]), indicador="IPC_variacion_mensual",
                        valor=row["valor"], fuente=FUENTE)
            for row in ipc if row["fecha"] >= "2022-01-01"
        ]
        db.bulk_save_objects(ipc_rows)
        db.commit()
        print(f"  ✅ {len(ipc_rows)} registros IPC insertados")

        # ── TC oficial (venta diaria) ────────────────────────────────────
        print("Descargando TC oficial...")
        tc = fetch("https://api.argentinadatos.com/v1/cotizaciones/dolares/oficial")
        tc_rows = [
            MacroIndice(fecha=_parse_fecha(row["fecha"]), indicador="TC_oficial_venta",
                        valor=row["venta"], fuente=FUENTE)
            for row in tc if row["fecha"] >= "2022-01-01"
        ]
        db.bulk_save_objects(tc_rows)
        db.commit()
        print(f"  ✅ {len(tc_rows)} registros TC insertados")

        # ── Resumen ───────────────────────────────────────────────────────
        from sqlalchemy import func
        resumen = (
            db.query(MacroIndice.indicador, func.count(MacroIndice.id),
                      func.min(MacroIndice.fecha), func.max(MacroIndice.fecha))
            .group_by(MacroIndice.indicador)
            .all()
        )
        print("\nResumen macro_indices:")
        for indicador, cnt, fmin, fmax in resumen:
            print(f"  {indicador:30} | {cnt} registros | {fmin} → {fmax}")

        # ── Exportar CSV versionable ─────────────────────────────────────
        os.makedirs(SEEDS_DIR, exist_ok=True)
        todos = (
            db.query(MacroIndice.fecha, MacroIndice.indicador, MacroIndice.valor, MacroIndice.fuente)
            .order_by(MacroIndice.indicador, MacroIndice.fecha)
            .all()
        )
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["fecha", "indicador", "valor", "fuente"])
            for fecha, indicador, valor, fuente in todos:
                fecha_str = fecha.strftime("%Y-%m-%d") if hasattr(fecha, "strftime") else fecha
                writer.writerow([fecha_str, indicador, valor, fuente])
        print(f"\n  → CSV exportado: {CSV_PATH} ({len(todos)} filas)")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
