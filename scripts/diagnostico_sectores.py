"""
diagnostico_sectores.py — v2
Ejecutar en el entorno con la DB real:
  python diagnostico_sectores.py
"""
import sys
sys.path.insert(0, '.')
from app.database.session import SessionLocal
from app.database import models
from sqlalchemy import func

db = SessionLocal()

print("=" * 70)
print("1. JURISDICCIONES 2023 (ministerios pre-fusión)")
print("=" * 70)
jurs = db.query(
    models.PresupuestoBase.jurisdiccion_id,
    models.PresupuestoBase.jurisdiccion_desc,
    func.sum(models.PresupuestoBase.monto_original).label("total")
).filter(models.PresupuestoBase.ejercicio == 2023).group_by(
    models.PresupuestoBase.jurisdiccion_id,
    models.PresupuestoBase.jurisdiccion_desc,
).order_by(func.sum(models.PresupuestoBase.monto_original).desc()).all()
for j in jurs:
    print(f"  {str(j.jurisdiccion_id):5s} | {j.total/1e9:10.1f}B | {j.jurisdiccion_desc}")

print()
print("=" * 70)
print("2. FACTORES IPC DESDE macro_indices (indicador='IPC')")
print("=" * 70)
try:
    rows = db.query(models.MacroIndice).filter(
        models.MacroIndice.indicador == 'IPC'
    ).order_by(models.MacroIndice.fecha).all()
    if rows:
        # Base = primer valor
        base = rows[0].valor
        print(f"  Base (primer registro): {rows[0].fecha} = {base}")
        # Factor al cierre de cada año
        por_anio = {}
        for r in rows:
            anio = r.fecha.year if hasattr(r.fecha, 'year') else int(str(r.fecha)[:4])
            por_anio[anio] = r.valor
        for anio, val in sorted(por_anio.items()):
            factor = val / base if base else None
            print(f"  {anio}: IPC nivel={val:.2f} → factor vs base={factor:.4f}" if factor else f"  {anio}: {val}")
    else:
        # Ver todos los indicadores disponibles
        inds = db.query(models.MacroIndice.indicador, func.count().label('n')).group_by(models.MacroIndice.indicador).all()
        print(f"  ⚠️  Sin filas con indicador='IPC'. Indicadores disponibles:")
        for i in inds:
            print(f"    '{i.indicador}': {i.n} registros")
        # Mostrar muestra de la tabla
        sample = db.query(models.MacroIndice).limit(5).all()
        print("  Muestra de la tabla:")
        for r in sample:
            print(f"    fecha={r.fecha} | indicador='{r.indicador}' | valor={r.valor}")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=" * 70)
print("3. PROGRAMAS CLAVE POR SECTOR EN 2023")
print("=" * 70)
SECTORES_2023 = {
    'jubilaciones': ['75', '91'],
    'ninez':        ['85'],
    'educacion':    ['70'],
    'obra-publica': ['64', '57', '65'],
    'salud':        ['80'],
}
for sector, jids in SECTORES_2023.items():
    rows = db.query(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_id,
        func.sum(models.PresupuestoBase.monto_original).label("total")
    ).filter(
        models.PresupuestoBase.ejercicio == 2023,
        models.PresupuestoBase.jurisdiccion_id.in_(jids)
    ).group_by(
        models.PresupuestoBase.programa_id,
        models.PresupuestoBase.programa_desc,
        models.PresupuestoBase.jurisdiccion_id,
    ).order_by(func.sum(models.PresupuestoBase.monto_original).desc()).limit(8).all()
    total_sector = sum(r.total for r in rows)
    print(f"\n  [{sector.upper()}] jur_ids={jids} — top programas:")
    for r in rows:
        print(f"    jur={r.jurisdiccion_id} prg={str(r.programa_id):5s} | {r.total/1e9:8.1f}B | {r.programa_desc}")

db.close()
print("\n✅ Diagnóstico v2 completo")