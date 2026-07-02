"""
check_sector_salud.py — verifica datos de Salud en la DB local
Columnas reales: monto_original, monto_vigente | jurisdiccion_id VARCHAR
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./sql_app.db")
engine = create_engine(DB_URL)
Session = sessionmaker(bind=engine)

with Session() as db:

    # ── 1. Ejercicios disponibles ──────────────────────────────────────────
    print("=== EJERCICIOS disponibles en presupuesto_base ===")
    rows = db.execute(text(
        "SELECT ejercicio, COUNT(*) as partidas "
        "FROM presupuesto_base GROUP BY ejercicio ORDER BY ejercicio"
    )).fetchall()
    for r in rows:
        print(f"  ejercicio={r[0]}  partidas={r[1]}")

    # ── 2. Salud jur_id='80' por ejercicio ────────────────────────────────
    print("\n=== Salud jurisdiccion_id='80' por ejercicio ===")
    rows = db.execute(text(
        "SELECT ejercicio, COUNT(*) as partidas, "
        "SUM(monto_original) as original, SUM(monto_vigente) as vigente "
        "FROM presupuesto_base WHERE jurisdiccion_id='80' "
        "GROUP BY ejercicio ORDER BY ejercicio"
    )).fetchall()
    for r in rows:
        print(f"  {r[0]}: partidas={r[1]}  original={r[2]:,.0f}  vigente={r[3]:,.0f}")

    # ── 3. Columnas de presupuesto_base ───────────────────────────────────
    print("\n=== Columnas de presupuesto_base ===")
    cols = db.execute(text("PRAGMA table_info(presupuesto_base)")).fetchall()
    for c in cols:
        print(f"  {c[1]:40s}  {c[2]}")

print("\n✅ Verificación completa — sin errores.")