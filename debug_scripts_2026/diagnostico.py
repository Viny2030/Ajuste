import sys
sys.path.insert(0, '.')

from app.database.session import SessionLocal
from app.database import models
from sqlalchemy import func

db = SessionLocal()

# Base 2023
r = db.query(
    func.count(models.PresupuestoBase.id),
    func.avg(models.PresupuestoBase.monto_original),
    func.avg(models.PresupuestoBase.monto_vigente),
    func.sum(models.PresupuestoBase.monto_original),
).first()

print(f"BASE 2023 — filas: {r[0]:,} | avg original: {r[1]:,.0f} | total: {r[3]/1e12:.4f}B")

# Modificaciones
m = db.query(
    func.count(models.ModificacionPresupuestaria.id),
    func.avg(models.ModificacionPresupuestaria.monto_neto),
    func.sum(models.ModificacionPresupuestaria.monto_neto),
    func.sum(models.ModificacionPresupuestaria.aumento),
    func.sum(models.ModificacionPresupuestaria.reduccion),
).filter(
    models.ModificacionPresupuestaria.norma_id.like("CREDITO-VIGENTE-%")
).first()

print(f"MODS — filas: {m[0]:,} | avg neto: {m[1]:,.0f} | sum neto: {m[2]/1e12:.4f}B")
print(f"  aumentos: {m[3]/1e12:.4f}B | reducciones: {m[4]/1e12:.4f}B")

db.close()