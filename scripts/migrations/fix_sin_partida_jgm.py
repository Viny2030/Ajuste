"""
Migración: linkeo manual de 6 modificaciones JGM sin FK a presupuesto_base
Fecha: 2026-05-16

Problema: 6 modificaciones de JGM (jurisdiccion_id='25') tenían partida_id NULL
porque el match por programa_id fallaba al comparar '09' vs '9' (zero-padding).

Mapeo aplicado:
  DA-280-2024, DA-858-2024, DA-1104-2024 → pb.id=532362 (JGM, prog=9, ej=2024)
  DA-10-2025                              → pb.id=650302 (SecInnovación, prog=9, ej=2025)
  DA-425-2025 (x2)                        → pb.id=651294 (VicejefInterior, prog=8, ej=2025)
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'sql_app.db')

updates = [
    (532362, 2622),   # DA-280-2024  → JGM prog=9 ej=2024
    (532362, 2112),   # DA-858-2024  → JGM prog=9 ej=2024
    (532362, 3151),   # DA-1104-2024 → JGM prog=9 ej=2024
    (650302, 2154),   # DA-10-2025   → SecInnovación prog=9 ej=2025
    (651294, 3367),   # DA-425-2025  → VicejefInterior prog=8 ej=2025
    (651294, 3392),   # DA-425-2025  → VicejefInterior prog=8 ej=2025
]

def run():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("Aplicando linkeo manual JGM sin partida...")
    for pb_id, mod_id in updates:
        cur.execute("UPDATE modificaciones SET partida_id = ? WHERE id = ?", (pb_id, mod_id))
        print(f"  mod_id={mod_id} → partida_id={pb_id} ({cur.rowcount} fila)")

    conn.commit()

    cur.execute("""
        SELECT COUNT(*) FROM modificaciones
        WHERE jurisdiccion_id = '25' AND partida_id IS NULL
    """)
    restantes = cur.fetchone()[0]
    print(f"\nModificaciones JGM sin partida restantes: {restantes}")
    assert restantes == 0, "⚠ Quedan modificaciones sin linkear"
    print("✅ Migración completada.")
    conn.close()

if __name__ == '__main__':
    run()