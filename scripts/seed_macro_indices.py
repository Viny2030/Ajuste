"""
scripts/seed_macro_indices.py
─────────────────────────────
Pobla macro_indices con:
  - IPC mensual (variación %) desde argentinadatos.com
  - TC oficial diario (venta) desde argentinadatos.com

Además exporta data/seeds/macro_indices.csv para versionado en git
(sql_app.db está en .gitignore y no se versiona).

En CI (GitHub Actions) la DB se crea desde cero en cada run.
"""

import csv
import json
import os
import sqlite3
import urllib.request

DB_PATH    = os.path.join(os.path.dirname(__file__), '..', 'sql_app.db')
SEEDS_DIR  = os.path.join(os.path.dirname(__file__), '..', 'data', 'seeds')
CSV_PATH   = os.path.join(SEEDS_DIR, 'macro_indices.csv')
FUENTE     = 'argentinadatos.com'


def fetch(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def seed():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # Crear tabla si no existe (útil en CI donde la DB arranca vacía)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS macro_indices (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha     TEXT NOT NULL,
            indicador TEXT NOT NULL,
            valor     REAL NOT NULL,
            fuente    TEXT
        )
    """)

    # Limpiar registros anteriores de esta fuente
    cur.execute("DELETE FROM macro_indices WHERE fuente = ?", (FUENTE,))

    # ── IPC mensual ───────────────────────────────────────────────────────────
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

    # ── TC oficial (venta diaria) ─────────────────────────────────────────────
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

    # ── Resumen ───────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT indicador, COUNT(*), MIN(fecha), MAX(fecha)
        FROM macro_indices
        GROUP BY indicador
    """)
    print("\nResumen macro_indices:")
    for r in cur.fetchall():
        print(f"  {r[0]:30} | {r[1]} registros | {r[2]} → {r[3]}")

    # ── Exportar CSV versionable ──────────────────────────────────────────────
    os.makedirs(SEEDS_DIR, exist_ok=True)
    cur.execute("""
        SELECT fecha, indicador, valor, fuente
        FROM macro_indices
        ORDER BY indicador, fecha
    """)
    rows = cur.fetchall()
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['fecha', 'indicador', 'valor', 'fuente'])
        writer.writerows(rows)
    print(f"\n  → CSV exportado: {CSV_PATH} ({len(rows)} filas)")

    conn.close()


if __name__ == '__main__':
    seed()