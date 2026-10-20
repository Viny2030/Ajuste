"""
scripts/seed_macro_indices.py
─────────────────────────────
Pobla macro_indices con:
  - IPC mensual (variación %) desde argentinadatos.com
  - TC oficial diario (venta, minorista BNA) desde argentinadatos.com
  - TC mayorista de referencia (id 5) desde la API BCRA v4.0 — la misma serie
    que usa app/core/deflactor.py como fuente principal; es la que el
    deflactor toma del CSV cuando el BCRA no responde.

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
FUENTE_BCRA = "BCRA v4.0"


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _parse_fecha(s: str) -> datetime:
    """Las APIs devuelven 'YYYY-MM-DD'; MacroIndice.fecha es DateTime."""
    return datetime.strptime(s, "%Y-%m-%d")


def _descargar_argentinadatos() -> list[tuple[str, str, float]]:
    ipc = fetch("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
    tc = fetch("https://api.argentinadatos.com/v1/cotizaciones/dolares/oficial")
    filas = [(r["fecha"], "IPC_variacion_mensual", r["valor"]) for r in ipc if r["fecha"] >= "2022-01-01"]
    filas += [(r["fecha"], "TC_oficial_venta", r["venta"]) for r in tc if r["fecha"] >= "2022-01-01"]
    if not any(f[1] == "IPC_variacion_mensual" for f in filas) or not any(f[1] == "TC_oficial_venta" for f in filas):
        raise ValueError("argentinadatos devolvió series vacías")
    return filas


def _descargar_bcra() -> list[tuple[str, str, float]]:
    """TC mayorista (id 5) de la API BCRA v4.0: la MISMA serie que usa la app
    como fuente principal. Así el respaldo (CSV) reproduce exactamente las
    cifras publicadas. No se usa el mayorista de argentinadatos: entre
    dic-2023 y ene-2024 trae valores atrasados (368 vs 642 del BCRA)."""
    from app.core.engine import _fetch_bcra_v4
    tc = _fetch_bcra_v4(5, desde="2022-01-01")
    if not tc:
        raise ValueError("BCRA v4.0 no devolvió la serie id 5")
    return [(r["fecha"][:10], "TC_mayorista_bcra", r["valor"]) for r in tc]


def seed() -> int:
    print(f"Conectando a: {DATABASE_URL[:45]}...")
    Base.metadata.create_all(bind=engine)  # asegura que la tabla exista

    # Primero se descarga TODO y recién después se toca la base. Antes se
    # borraba y confirmaba el borrado antes de descargar: si la API fallaba,
    # la tabla quedaba vacía. Ahora cada fuente se reemplaza en su propia
    # transacción y sólo si su descarga salió bien; si una falla se conservan
    # sus datos anteriores y el script termina con error (el workflow no
    # commitea un CSV incompleto).
    fallas = []
    descargas = {}
    for fuente, funcion in ((FUENTE, _descargar_argentinadatos), (FUENTE_BCRA, _descargar_bcra)):
        print(f"Descargando {fuente}...")
        try:
            descargas[fuente] = funcion()
            print(f"  ✅ {len(descargas[fuente])} registros")
        except Exception as e:
            fallas.append(fuente)
            print(f"  ❌ {fuente}: {e} — se conservan los datos anteriores")

    db = SessionLocal()
    try:
        for fuente, filas in descargas.items():
            borrados = db.query(MacroIndice).filter(MacroIndice.fuente == fuente).delete()
            db.bulk_save_objects([
                MacroIndice(fecha=_parse_fecha(f), indicador=ind, valor=v, fuente=fuente)
                for f, ind, v in filas
            ])
            db.commit()
            print(f"  🔁 {fuente}: {borrados} registros reemplazados por {len(filas)}")

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

    if fallas:
        print(f"\n❌ Fallaron: {', '.join(fallas)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(seed())
