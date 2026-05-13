# scripts/seed_2023.py
"""
Carga inicial del Presupuesto 2023 desde datos.gob.ar (Hacienda / SSPRE).

Dataset oficial:
  https://datos.gob.ar/id/dataset/sspre-presupuesto-administracion-publica-nacional-2023

Archivo usado:
  "Presupuesto de gastos y su ejecución detallada - agrupación anual 2023"
  ZIP con CSV de todos los clasificadores (JUR/SAF/PRG/INC/PPA/FF + crédito original y vigente)

Ejecución:
  python -m scripts.seed_2023
  python -m scripts.seed_2023 --csv data/credito-anual-2023.csv   (si ya tenés el CSV)
"""
import argparse
import io
import os
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.session import SessionLocal, engine
from app.database import models

# ── URL oficial (dgsiaf-repo.mecon.gob.ar) ──────────────────────
URL_ZIP = "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2023/credito-anual-2023.zip"

# ── Mapeo flexible de columnas ───────────────────────────────────
MAPA = {
    "ejercicio": "ejercicio",
    "jurisdiccion_id": "jurisdiccion_id",
    "jurisdiccion_desc": "jurisdiccion_desc",
    "servicio_id": "entidad_id",
    "servicio_desc": "entidad_desc",
    "programa_id": "programa_id",
    "programa_desc": "programa_desc",
    "subprograma_id": "subprograma_id",
    "proyecto_id": "proyecto_id",
    "actividad_id": "actividad_id",
    "obra_id": "obra_id",
    "inciso_id": "inciso_id",
    "inciso_desc": "inciso_desc",
    "principal_id": "principal_id",
    "principal_desc": "principal_desc",
    "parcial_id": "parcial_id",
    "parcial_desc": "parcial_desc",
    "subparcial_id": "subparcial_id",
    "subparcial_desc": "subparcial_desc",
    "fuente_id": "fuente_financiamiento_id",
    "fuente_desc": "fuente_financiamiento_desc",
    "ubicacion_geografica_id": "ubicacion_geografica_id",
    "credito_presupuestado": "monto_original",
    "credito_vigente": "monto_vigente",
    # fallbacks versiones anteriores
    "jur_id": "jurisdiccion_id",
    "ent_id": "entidad_id",
    "prg_id": "programa_id",
    "inc_id": "inciso_id",
    "ppa_id": "principal_id",
    "ff_id": "fuente_financiamiento_id",
}


def _descargar_zip() -> pd.DataFrame:
    print(f"⬇️  Descargando ZIP desde dgsiaf-repo.mecon.gob.ar...")
    os.makedirs("data", exist_ok=True)
    try:
        resp = requests.get(URL_ZIP, timeout=180, stream=True)
        resp.raise_for_status()
        content = b"".join(resp.iter_content(chunk_size=65536))
        print(f"✅ ZIP descargado ({len(content) / 1_048_576:.1f} MB)")
    except Exception as e:
        print(f"❌ Descarga fallida: {e}")
        print("\n👉 Descargá manualmente el ZIP desde:")
        print("   https://datos.gob.ar/id/dataset/sspre-presupuesto-administracion-publica-nacional-2023")
        print("   Archivo: 'Presupuesto de gastos y su ejecución detallada - agrupación anual 2023'")
        print(f"   Luego: python -m scripts.seed_2023 --csv data/credito-anual-2023.csv")
        sys.exit(1)

    with zipfile.ZipFile(io.BytesIO(content)) as z:
        nombres = z.namelist()
        print(f"   Archivos en ZIP: {nombres}")
        csv_name = next((n for n in nombres if n.lower().endswith(".csv")), None)
        if not csv_name:
            print("❌ No se encontró CSV dentro del ZIP")
            sys.exit(1)
        z.extract(csv_name, "data/")
        csv_path = f"data/{csv_name}"
        print(f"   CSV extraído en: {csv_path}")
        return _leer_csv(csv_path)


def _leer_csv(path: str) -> pd.DataFrame:
    print(f"📖 Leyendo: {path}")
    for enc in ("utf-8-sig", "latin-1", "utf-8"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo leer {path}")


def _normalizar(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]

    # ── FIX: el CSV de Hacienda tiene "entidad_id" duplicado (una col de id y
    #    una de desc con mismo nombre). Pandas los lee como entidad_id / entidad_id.1
    #    Renombramos las .1 antes del mapeo general. ──────────────────────────
    rename_dupes = {}
    seen = {}
    new_cols = []
    for col in df.columns:
        base = col.rstrip(".0123456789")
        if col.endswith(".1") and base in seen:
            rename_dupes[col] = base + "_desc_dup"
        seen[col] = True
        new_cols.append(col)
    if rename_dupes:
        df = df.rename(columns=rename_dupes)

    df = df.rename(columns=MAPA)
    print(f"   Columnas normalizadas: {df.columns.tolist()[:15]}{'...' if len(df.columns) > 15 else ''}")

    # Filtrar solo 2023
    for col_anio in ("ejercicio_presupuestario", "impacto_presupuestario_anio", "ejercicio"):
        if col_anio in df.columns:
            df = df[df[col_anio].astype(str).str.strip() == "2023"]
            break

    # Los montos del CSV de la ONP están en MILLONES de pesos (coma decimal).
    # Ej: 18585,776825 → $18.585.776.825 pesos → multiplicar por 1_000_000
    for col in ("monto_original", "monto_vigente"):
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace(r"[^\d\.]", "", regex=True)
                .pipe(pd.to_numeric, errors="coerce")
                .fillna(0.0)
                .mul(1_000_000)
                .round(0)
            )
    return df


def cargar_presupuesto_2023(csv_path: str | None = None):
    models.Base.metadata.create_all(bind=engine)

    if csv_path and Path(csv_path).exists():
        df = _leer_csv(csv_path)
    else:
        df = _descargar_zip()

    df = _normalizar(df)

    if df.empty:
        print("❌ DataFrame vacío. Revisá el formato del CSV.")
        return

    print(f"🚀 Insertando {len(df):,} partidas...")
    db: Session = SessionLocal()
    insertados = errores = 0

    try:
        for _, row in df.iterrows():
            try:
                db.add(models.PresupuestoBase(
                    ejercicio=2023,
                    jurisdiccion_id=_v(row, "jurisdiccion_id"),
                    jurisdiccion_desc=_v(row, "jurisdiccion_desc"),
                    entidad_id=_v(row, "entidad_id"),
                    entidad_desc=_v(row, "entidad_desc"),
                    programa_id=_v(row, "programa_id"),
                    programa_desc=_v(row, "programa_desc"),
                    subprograma_id=_v(row, "subprograma_id"),
                    proyecto_id=_v(row, "proyecto_id"),
                    actividad_id=_v(row, "actividad_id"),
                    obra_id=_v(row, "obra_id"),
                    inciso_id=_v(row, "inciso_id"),
                    inciso_desc=_v(row, "inciso_desc"),
                    principal_id=_v(row, "principal_id"),
                    principal_desc=_v(row, "principal_desc"),
                    parcial_id=_v(row, "parcial_id"),
                    parcial_desc=_v(row, "parcial_desc"),
                    subparcial_id=_v(row, "subparcial_id"),
                    subparcial_desc=_v(row, "subparcial_desc"),
                    fuente_financiamiento_id=_v(row, "fuente_financiamiento_id"),
                    fuente_financiamiento_desc=_v(row, "fuente_financiamiento_desc"),
                    ubicacion_geografica_id=_v(row, "ubicacion_geografica_id"),
                    monto_original=float(row.get("monto_original") or 0),
                    monto_vigente=float(row.get("monto_vigente") or 0),
                ))
                insertados += 1
                if insertados % 5000 == 0:
                    db.commit()
                    print(f"   ... {insertados:,} filas")
            except Exception as e:
                db.rollback()
                errores += 1
                if errores <= 3:
                    print(f"  ⚠️ Error fila: {e}")

        db.commit()
        print(f"\n✅ Completado: {insertados:,} partidas | {errores} errores")
    except Exception as e:
        db.rollback()
        print(f"❌ Error crítico: {e}")
    finally:
        db.close()


def _v(row, col: str):
    val = row.get(col)
    if val is None:
        return None
    # Si pandas devolvio una Series por columna duplicada, tomar primer valor
    if isinstance(val, pd.Series):
        val = val.iloc[0]
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    return s if s not in ("", "nan") else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None, help="Path al CSV ya descomprimido")
    args = parser.parse_args()
    cargar_presupuesto_2023(args.csv)
