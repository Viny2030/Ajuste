from app.database.session import SessionLocal
from app.database import models
from sqlalchemy import func

db = SessionLocal()

resumen = db.query(
    models.ModificacionPresupuestaria.jurisdiccion_id,
    func.count().label('n'),
    func.sum(models.ModificacionPresupuestaria.aumento).label('aumento'),
    func.sum(models.ModificacionPresupuestaria.reduccion).label('reduccion'),
).group_by(
    models.ModificacionPresupuestaria.jurisdiccion_id
).order_by('jurisdiccion_id').all()

print(f"{'JUR':>4}  {'MODS':>6}  {'AUMENTO':>25}  {'REDUCCION':>25}")
print('-' * 65)
for r in resumen:
    jur = r.jurisdiccion_id or '?'
    print(f"{jur:>4}  {r.n:>6}  {r.aumento:>25,.0f}  {r.reduccion:>25,.0f}")

total_mods = sum(r.n for r in resumen)
total_aum  = sum(r.aumento or 0 for r in resumen)
total_red  = sum(r.reduccion or 0 for r in resumen)
print('-' * 65)
print(f"{'TOT':>4}  {total_mods:>6}  {total_aum:>25,.0f}  {total_red:>25,.0f}")

db.close()