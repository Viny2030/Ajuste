"""
scripts/seed_2026.py
Carga el crédito vigente 2026 desde el portal de Presupuesto Abierto del MECON.

Uso:
    python -m scripts.seed_2026

Con Railway remoto:
    $env:DATABASE_URL="postgresql://..."; python -m scripts.seed_2026
"""
import io
import os
import sys
import zipfile
import requests
import pandas as pd
from sqlalchemy import text

# ── path fix para correr desde raíz del proyecto ──────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.session import SessionLocal, engine

# ── URLs a probar en orden ─────────────────────────────────────────────────────
URLS_ZIP = [
    "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2026/credito-anual-2026.zip",
    "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2025/credito-anual-2025.zip",  # fallback
]

COLUMNAS_MAP = {
    # nombre en CSV → nombre en modelo
    "ejercicio_presupuestario":         "ejercicio",
    "impacto_presupuestario_anio":      "ejercicio",
    "jurisdiccion_id":                  "jurisdiccion_id",
    "jurisdiccion_desc":                "jurisdiccion_desc",
    "entidad_id":                       "entidad_id",
    "entidad_desc":                     "entidad_desc",
    "programa_id":                      "programa_id",
    "programa_desc":                    "programa_desc",
    "subprograma_id":                   "subprograma_id",
    "proyecto_id":                      "proyecto_id",
    "actividad_id":                     "actividad_id",
    "obra_id":                          "obra_id",
    "inciso_id":                        "inciso_id",
    "inciso_desc":                      "inciso_desc",
    "principal_id":                     "principal_id",
    "principal_desc":                   "principal_desc",
    "parcial_id":                       "parcial_id",
    "parcial_desc":                     "parcial_desc",
    "subparcial_id":                    "subparcial_id",
    "subparcial_desc":                  "subparcial_desc",
    "fuente_financiamiento_id":         "fuente_financiamiento_id",
    "fuente_financiamiento_desc":       "fuente_financiamiento_desc",
    "ubicacion_geografica_id":          "ubicacion_geografica_id",
    # montos — el CSV 2026 puede llamarlos distinto
    "credito_original":                 "monto_original",
    "credito_vigente":                  "monto_vigente",
    "monto_original":                   "monto_original",
    "monto_vigente":                    "monto_vigente",
    "importe_original":                 "monto_original",
    "importe_vigente":                  "monto_vigente",
    "credito_presupuestario_original":  "monto_original",
    "credito_presupuestario_vigente":   "monto_vigente",
}

EJERCICIO = 2026
BATCH     = 5_000


def _descargar_csv() -> pd.DataFrame:
    for url in URLS_ZIP:
        print(f"⬇️  Intentando: {url}")
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            print(f"   ✅ Descargado ({len(r.content)/1024/1024:.1f} MB)")

            if url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csvs = [n for n in z.namelist() if n.endswith(".csv")]
                    print(f"   Archivos en ZIP: {csvs}")
                    if not csvs:
                        print("   ⚠️  ZIP sin CSV, probando siguiente URL...")
                        continue
                    with z.open(csvs[0]) as f:
                        df = pd.read_csv(f, dtype=str, encoding="utf-8", sep=None, engine="python")
            else:
                df = pd.read_csv(io.BytesIO(r.content), dtype=str, encoding="utf-8", sep=None, engine="python")

            print(f"   Columnas originales: {df.columns.tolist()[:8]}...")
            return df

        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue

    raise RuntimeError("No se pudo descargar el CSV 2026 desde ninguna URL.")


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    # normalizar nombres de columna
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    renombres = {k: v for k, v in COLUMNAS_MAP.items() if k in df.columns}
    df = df.rename(columns=renombres)
    print(f"   Columnas normalizadas: {df.columns.tolist()[:10]}...")

    # ejercicio
    if "ejercicio" not in df.columns:
        df["ejercicio"] = EJERCICIO
    else:
        df["ejercicio"] = pd.to_numeric(df["ejercicio"], errors="coerce").fillna(EJERCICIO).astype(int)
        # filtrar solo 2026 por si acaso trae varios años
        df = df[df["ejercicio"] == EJERCICIO]

    # montos
    for col in ("monto_original", "monto_vigente"):
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ".").str.replace(" ", ""),
                errors="coerce"
            ).fillna(0.0)
        else:
            df[col] = 0.0

    # rellenar campos opcionales
    opcionales = [
        "subprograma_id", "proyecto_id", "actividad_id", "obra_id",
        "principal_id", "principal_desc", "parcial_id", "parcial_desc",
        "subparcial_id", "subparcial_desc", "fuente_financiamiento_id",
        "fuente_financiamiento_desc", "ubicacion_geografica_id",
    ]
    for col in opcionales:
        if col not in df.columns:
            df[col] = None

    return df


def seed(df: pd.DataFrame, db) -> None:
    print(f"\n🗑️  Eliminando registros anteriores de ejercicio={EJERCICIO}...")
    db.execute(text("DELETE FROM presupuesto_base WHERE ejercicio = :anio"), {"anio": EJERCICIO})
    db.commit()
    print(f"   ✅ Eliminados")

    total  = len(df)
    errores = 0
    print(f"\n🚀 Insertando {total:,} partidas ejercicio {EJERCICIO}...")

    COLS = [
        "ejercicio", "jurisdiccion_id", "jurisdiccion_desc", "entidad_id", "entidad_desc",
        "programa_id", "programa_desc", "subprograma_id", "proyecto_id", "actividad_id",
        "obra_id", "inciso_id", "inciso_desc", "principal_id", "principal_desc",
        "parcial_id", "parcial_desc", "subparcial_id", "subparcial_desc",
        "fuente_financiamiento_id", "fuente_financiamiento_desc",
        "ubicacion_geografica_id", "monto_original", "monto_vigente",
    ]

    for i in range(0, total, BATCH):
        batch = df.iloc[i : i + BATCH]
        rows  = []
        for _, row in batch.iterrows():
            try:
                rows.append({c: (None if pd.isna(row.get(c)) else row.get(c)) for c in COLS})
            except Exception as e:
                errores += 1
                if errores <= 3:
                    print(f"   ⚠️  Fila {i}: {e}")

        if rows:
            db.execute(text(f"""
                INSERT INTO presupuesto_base
                    ({', '.join(COLS)})
                VALUES
                    ({', '.join(':' + c for c in COLS)})
            """), rows)
            db.commit()

        print(f"   ... {min(i + BATCH, total):,} / {total:,} filas")

    print(f"\n✅ Completado: {total - errores:,} partidas | {errores} errores")

    # verificación rápida
    print("\n=== Verificación post-seed ===")
    r = db.execute(text("""
        SELECT jurisdiccion_desc, COUNT(*), SUM(monto_vigente)
        FROM presupuesto_base
        WHERE ejercicio = 2026
        GROUP BY jurisdiccion_desc
        ORDER BY SUM(monto_vigente) DESC
        LIMIT 8
    """)).fetchall()
    for row in r:
        print(f"  {row[0]:45s}  {row[1]:6,} partidas  ${row[2]/1e9:10.1f} B")


def main():
    print(f"=== SEED PRESUPUESTO {EJERCICIO} ===\n")
    df = _descargar_csv()
    df = _normalizar(df)
    print(f"\n📊 Filas a insertar: {len(df):,}")

    db = SessionLocal()
    try:
        seed(df, db)
    finally:
        db.close()

    print("\n🎉 Seed 2026 completo. Probá:")
    print("   curl https://ajuste-production.up.railway.app/api/v1/analisis/sector?sector=salud")


if __name__ == "__main__":
    main()