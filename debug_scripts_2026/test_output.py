from app.database.session import SessionLocal
from app.database.models import ModificacionPresupuestaria

db = SessionLocal()
cambios = db.query(ModificacionPresupuestaria).all()
print(f"Total de modificaciones detectadas: {len(cambios)}")
for c in cambios:
    print(f"Norma: {c.norma_id} | Impacto Neto: {c.monto_neto}")
db.close()