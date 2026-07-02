import os
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session
from app.database.models import NormaJGM, ModificacionPresupuestaria

engine = create_engine(os.environ["DATABASE_URL"])

with Session(engine) as db:
    sin_tabla = (
        db.query(NormaJGM.norma_id, NormaJGM.titulo)
        .outerjoin(ModificacionPresupuestaria, ModificacionPresupuestaria.norma_id == NormaJGM.norma_id)
        .group_by(NormaJGM.norma_id, NormaJGM.titulo)
        .having(func.count(ModificacionPresupuestaria.id) == 0)
        .order_by(NormaJGM.norma_id)
        .all()
    )
    print(f"DAs sin modificaciones: {len(sin_tabla)}\n")
    for row in sin_tabla:
        print(f"{row.norma_id:20} | {row.titulo[:80]}")