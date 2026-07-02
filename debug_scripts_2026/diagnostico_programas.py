"""
diagnostico_programas.py
Diagnóstico exhaustivo de estabilidad de programas presupuestarios entre 2023-2026.

Genera tres archivos en data/analisis/:
  1. programas_por_ministerio.csv   — todos los programas por jurisdicción y año
  2. programas_match.csv            — análisis de continuidad por programa_id
  3. programas_conflictos.csv       — casos donde mismo programa_id tiene distinto nombre

Uso:
  python diagnostico_programas.py
  python diagnostico_programas.py --sector jubilaciones
  python diagnostico_programas.py --jur 64 75 88
"""
import sys, os, argparse, csv
from collections import defaultdict

sys.path.insert(0, '.')
from app.database.session import SessionLocal
from app.database import models
from sqlalchemy import func

parser = argparse.ArgumentParser()
parser.add_argument('--sector', default=None)
parser.add_argument('--jur', nargs='*', default=None)
args = parser.parse_args()

# Sectores predefinidos para filtro rápido
SECTORES = {
    'jubilaciones': {'2023': ['75','91'], '2024': ['88','91'], '2025': ['88','91'], '2026': ['88','91']},
    'ninez':        {'2023': ['85'],      '2024': ['88'],      '2025': ['88'],      '2026': ['88']},
    'educacion':    {'2023': ['70'],      '2024': ['88'],      '2025': ['88'],      '2026': ['88']},
    'obra-publica': {'2023': ['64','57','65'], '2024': ['64','57','65'], '2025': ['64','57','65'], '2026': ['64','57','65']},
    'salud':        {'2023': ['80'],      '2024': ['80'],      '2025': ['80'],      '2026': ['80']},
}

db = SessionLocal()
os.makedirs('data/analisis', exist_ok=True)

print("Cargando programas de la DB...")

# Construir filtro de jurisdicciones
jur_filter = None
if args.jur:
    jur_filter = args.jur
elif args.sector and args.sector in SECTORES:
    # Unión de todas las jurisdicciones del sector en todos los años
    jur_filter = list(set(j for jurs in SECTORES[args.sector].values() for j in jurs))
    print(f"Sector '{args.sector}' → jurisdicciones: {jur_filter}")

# Consulta base
q = db.query(
    models.PresupuestoBase.ejercicio,
    models.PresupuestoBase.jurisdiccion_id,
    models.PresupuestoBase.jurisdiccion_desc,
    models.PresupuestoBase.programa_id,
    models.PresupuestoBase.programa_desc,
    func.sum(models.PresupuestoBase.monto_original).label('monto_original'),
    func.sum(models.PresupuestoBase.monto_vigente).label('monto_vigente'),
).group_by(
    models.PresupuestoBase.ejercicio,
    models.PresupuestoBase.jurisdiccion_id,
    models.PresupuestoBase.jurisdiccion_desc,
    models.PresupuestoBase.programa_id,
    models.PresupuestoBase.programa_desc,
).order_by(
    models.PresupuestoBase.jurisdiccion_id,
    models.PresupuestoBase.programa_id,
    models.PresupuestoBase.ejercicio,
)

if jur_filter:
    q = q.filter(models.PresupuestoBase.jurisdiccion_id.in_(jur_filter))

rows = q.all()
print(f"Total registros: {len(rows)}")

# ── 1. CSV completo por ministerio/programa/año ─────────────────────────────
out1 = 'data/analisis/programas_por_ministerio.csv'
with open(out1, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['ejercicio','jur_id','jur_desc','prg_id','prg_desc','monto_original','monto_vigente','monto_normalizado'])
    for r in rows:
        # Normalizar a pesos completos (2023 ya está en pesos, 2024+ en millones)
        if r.ejercicio == 2023:
            monto_norm = float(r.monto_original or 0)
        else:
            monto_norm = float(r.monto_vigente or 0) * 1_000_000
        w.writerow([
            r.ejercicio, r.jurisdiccion_id, r.jurisdiccion_desc,
            r.programa_id, r.programa_desc,
            round(float(r.monto_original or 0), 2),
            round(float(r.monto_vigente or 0), 2),
            round(monto_norm, 0),
        ])
print(f"✅ {out1}")

# ── 2. Análisis de match por (jur_id, prg_id) ───────────────────────────────
# Agrupa por jurisdicción + programa_id, muestra en qué años aparece y si el nombre cambió

# Índice: (jur_id, prg_id) → {año: {desc, monto}}
idx = defaultdict(dict)
for r in rows:
    key = (str(r.jurisdiccion_id), str(r.programa_id))
    monto = float(r.monto_original or 0) if r.ejercicio == 2023 else float(r.monto_vigente or 0) * 1_000_000
    idx[key][r.ejercicio] = {
        'desc': r.programa_desc,
        'monto': round(monto, 0),
        'jur_desc': r.jurisdiccion_desc,
    }

out2 = 'data/analisis/programas_match.csv'
with open(out2, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow([
        'jur_id','jur_desc','prg_id',
        'en_2023','en_2024','en_2025','en_2026',
        'monto_2023','monto_2024','monto_2025','monto_2026',
        'nombre_estable','nombres_distintos','alerta',
        'desc_2023','desc_2026',
    ])
    for (jid, pid), años in sorted(idx.items()):
        nombres = list({d['desc'] for d in años.values()})
        nombre_estable = len(nombres) == 1
        en = {a: a in años for a in [2023,2024,2025,2026]}
        montos = {a: años[a]['monto'] if a in años else 0 for a in [2023,2024,2025,2026]}
        jur_desc = next(iter(años.values()))['jur_desc']

        # Alertas
        alertas = []
        if not nombre_estable:
            alertas.append('NOMBRE_CAMBIA')
        if en[2023] and not any([en[2024],en[2025],en[2026]]):
            alertas.append('SOLO_2023')
        if not en[2023] and any([en[2024],en[2025],en[2026]]):
            alertas.append('NUEVO_POST2023')
        if en[2023] and en[2026] and montos[2026] > montos[2023] * 50:
            alertas.append('MONTO_X50')
        if en[2023] and en[2026] and montos[2026] < montos[2023] * 0.01:
            alertas.append('CASI_ELIMINADO')

        w.writerow([
            jid, jur_desc, pid,
            '✓' if en[2023] else '✗',
            '✓' if en[2024] else '✗',
            '✓' if en[2025] else '✗',
            '✓' if en[2026] else '✗',
            montos[2023], montos[2024], montos[2025], montos[2026],
            'SI' if nombre_estable else 'NO',
            ' | '.join(nombres) if not nombre_estable else '',
            ' | '.join(alertas) if alertas else '',
            años.get(2023, {}).get('desc',''),
            años.get(2026, {}).get('desc',''),
        ])
print(f"✅ {out2}")

# ── 3. CSV de conflictos (mismo ID, distinto nombre = posible reutilización) ─
out3 = 'data/analisis/programas_conflictos.csv'
conflictos = {k: v for k,v in idx.items()
              if len({d['desc'] for d in v.values()}) > 1}
print(f"\nPrograma_IDs con nombre inestable: {len(conflictos)}")

with open(out3, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['jur_id','jur_desc','prg_id','año','desc','monto_normalizado'])
    for (jid, pid), años in sorted(conflictos.items()):
        for año, data in sorted(años.items()):
            w.writerow([jid, data['jur_desc'], pid, año, data['desc'], data['monto']])
print(f"✅ {out3}")

# ── Resumen por sector ───────────────────────────────────────────────────────
print("\n" + "="*70)
print("RESUMEN POR JURISDICCIÓN")
print("="*70)
resumen = defaultdict(lambda: {'total':0,'solo_2023':0,'nuevo':0,'conflicto':0,'completo':0})
for (jid, pid), años in idx.items():
    jur_desc = next(iter(años.values()))['jur_desc']
    key = f"{jid} | {jur_desc}"
    resumen[key]['total'] += 1
    nombres = list({d['desc'] for d in años.values()})
    en = {a: a in años for a in [2023,2024,2025,2026]}
    if en[2023] and not any([en[2024],en[2025],en[2026]]): resumen[key]['solo_2023'] += 1
    if not en[2023] and any([en[2024],en[2025],en[2026]]): resumen[key]['nuevo'] += 1
    if len(nombres) > 1: resumen[key]['conflicto'] += 1
    if all(en.values()): resumen[key]['completo'] += 1

for key, v in sorted(resumen.items()):
    print(f"  {key}")
    print(f"    total={v['total']} | completo_4_años={v['completo']} | solo_2023={v['solo_2023']} | nuevo_post2023={v['nuevo']} | nombre_cambia={v['conflicto']}")

db.close()
print("\n✅ Diagnóstico completo. Archivos en data/analisis/")