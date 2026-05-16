# scripts/seed_macro_indices.py
"""
Pobla macro_indices con:
  - IPC mensual (variación %) desde argentinadatos.com
  - TC oficial diario (venta) desde argentinadatos.com
"""
import urllib.request
import json
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'sql_app.db')
FUENTE = 'argentinadatos.com'

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def seed():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Limpiar antes de reinsertar
    cur.execute("DELETE FROM macro_indices WHERE fuente = ?", (FUENTE,))

    # IPC mensual
    print("Descargando IPC...")
    ipc = fetch("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
    ipc_rows = [
        (row['fecha'], 'IPC_variacion_mensual', row['valor'], FUENTE)
        for row in ipc if row['fecha'] >= '2022-01-01'
    ]
    cur.executemany(
        "INSERT INTO macro_indices (fecha, indicador, valor, fuente) VALUES (?,?,?,?)",
        ipc_rows
    )
    print(f"  ✅ {len(ipc_rows)} registros IPC insertados")

    # TC oficial (venta, promedio diario)
    print("Descargando TC oficial...")
    tc = fetch("https://api.argentinadatos.com/v1/cotizaciones/dolares/oficial")
    tc_rows = [
        (row['fecha'], 'TC_oficial_venta', row['venta'], FUENTE)
        for row in tc if row['fecha'] >= '2022-01-01'
    ]
    cur.executemany(
        "INSERT INTO macro_indices (fecha, indicador, valor, fuente) VALUES (?,?,?,?)",
        tc_rows
    )
    print(f"  ✅ {len(tc_rows)} registros TC insertados")

    conn.commit()

    # Verificar
    cur.execute("SELECT indicador, COUNT(*), MIN(fecha), MAX(fecha) FROM macro_indices GROUP BY indicador")
    print("\nResumen macro_indices:")
    for r in cur.fetchall():
        print(f"  {r[0]:30} | {r[1]} registros | {r[2]} → {r[3]}")

    conn.close()

if __name__ == '__main__':
    seed()