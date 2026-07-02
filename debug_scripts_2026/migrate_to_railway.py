"""
migrate_to_railway.py
Migra presupuesto_base desde SQLite local → PostgreSQL Railway
Uso:
    $env:RAILWAY_DB="postgresql://..."
    $env:DATABASE_URL="sqlite:///./sql_app.db"
    python migrate_to_railway.py
"""
import os
import sys
from sqlalchemy import create_engine, text
import pandas as pd

SQLITE_URL  = os.environ.get("DATABASE_URL", "sqlite:///./sql_app.db")
RAILWAY_URL = os.environ.get("RAILWAY_DB", "")

if not RAILWAY_URL:
    print("ERROR: RAILWAY_DB no está seteada.")
    sys.exit(1)

print(f"Origen  : {SQLITE_URL}")
print(f"Destino : postgresql://...@shortline.proxy.rlwy.net:38307/railway")

src = create_engine(SQLITE_URL)
dst = create_engine(RAILWAY_URL)

# ── 1. Crear tabla en PostgreSQL si no existe ─────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS presupuesto_base (
    id                      SERIAL PRIMARY KEY,
    ejercicio               INTEGER,
    jurisdiccion_id         VARCHAR(3),
    jurisdiccion_desc       VARCHAR(200),
    entidad_id              VARCHAR(5),
    entidad_desc            VARCHAR(200),
    programa_id             VARCHAR(5),
    programa_desc           VARCHAR(300),
    subprograma_id          VARCHAR(5),
    proyecto_id             VARCHAR(5),
    actividad_id            VARCHAR(5),
    obra_id                 VARCHAR(5),
    inciso_id               VARCHAR(2),
    inciso_desc             VARCHAR(100),
    principal_id            VARCHAR(3),
    principal_desc          VARCHAR(100),
    parcial_id              VARCHAR(4),
    parcial_desc            VARCHAR(100),
    subparcial_id           VARCHAR(5),
    subparcial_desc         VARCHAR(100),
    fuente_financiamiento_id   VARCHAR(2),
    fuente_financiamiento_desc VARCHAR(100),
    ubicacion_geografica_id    VARCHAR(5),
    monto_original          FLOAT,
    monto_vigente           FLOAT
);
"""
with dst.connect() as c:
    c.execute(text(DDL))
    c.commit()
print("✅ Tabla presupuesto_base lista en PostgreSQL.")

# ── 2. Verificar qué ejercicios ya están en Railway ───────────────────────
with dst.connect() as c:
    existentes = [r[0] for r in c.execute(text(
        "SELECT DISTINCT ejercicio FROM presupuesto_base ORDER BY ejercicio"
    )).fetchall()]
print(f"Ejercicios ya en Railway: {existentes}")

# ── 3. Migrar ejercicio por ejercicio ─────────────────────────────────────
COLS = [
    "ejercicio","jurisdiccion_id","jurisdiccion_desc","entidad_id","entidad_desc",
    "programa_id","programa_desc","subprograma_id","proyecto_id","actividad_id",
    "obra_id","inciso_id","inciso_desc","principal_id","principal_desc",
    "parcial_id","parcial_desc","subparcial_id","subparcial_desc",
    "fuente_financiamiento_id","fuente_financiamiento_desc",
    "ubicacion_geografica_id","monto_original","monto_vigente"
]

with src.connect() as c:
    ejercicios = [r[0] for r in c.execute(text(
        "SELECT DISTINCT ejercicio FROM presupuesto_base ORDER BY ejercicio"
    )).fetchall()]

print(f"Ejercicios en SQLite: {ejercicios}")

for ej in ejercicios:
    if ej in existentes:
        print(f"  {ej}: ya existe en Railway, salteando.")
        continue
    print(f"  {ej}: migrando...", end=" ", flush=True)
    df = pd.read_sql(
        f"SELECT {', '.join(COLS)} FROM presupuesto_base WHERE ejercicio={ej}",
        src
    )
    df.to_sql("presupuesto_base", dst, if_exists="append", index=False, chunksize=5000)
    print(f"{len(df):,} partidas ✅")

print("\n🎉 Migración completa.")