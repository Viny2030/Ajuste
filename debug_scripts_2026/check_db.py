from app.database.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("=== TOP 10 jurisdicciones por cantidad de partidas ===")
r = db.execute(text(
    "SELECT COUNT(*), jurisdiccion_desc FROM presupuesto_base "
    "WHERE ejercicio=2023 GROUP BY jurisdiccion_desc ORDER BY COUNT(*) DESC LIMIT 10"
)).fetchall()
for row in r:
    print(row)

print("\n=== Buscar Salud (cualquier variante) ===")
r2 = db.execute(text(
    "SELECT DISTINCT jurisdiccion_id, jurisdiccion_desc FROM presupuesto_base "
    "WHERE LOWER(jurisdiccion_desc) LIKE '%salud%' OR LOWER(jurisdiccion_desc) LIKE '%sanidad%'"
)).fetchall()
for row in r2:
    print(row)

print("\n=== Buscar Capital Humano / Jubilaciones ===")
r3 = db.execute(text(
    "SELECT DISTINCT jurisdiccion_id, jurisdiccion_desc FROM presupuesto_base "
    "WHERE LOWER(jurisdiccion_desc) LIKE '%capital%' OR LOWER(jurisdiccion_desc) LIKE '%jubil%' "
    "OR LOWER(jurisdiccion_desc) LIKE '%anses%' OR LOWER(jurisdiccion_desc) LIKE '%trabajo%'"
)).fetchall()
for row in r3:
    print(row)

print("\n=== Total partidas en DB ===")
total = db.execute(text("SELECT COUNT(*) FROM presupuesto_base")).scalar()
print(f"Total: {total}")

db.close()