# scripts/sync_modificaciones.py
"""
Estrategia alternativa al scraping del BORA:
Descarga los CSVs de crédito anual de Hacienda para 2024 y 2025,
los cruza con la base 2023, y calcula las reducciones reales por partida.

Fuente: dgsiaf-repo.mecon.gob.ar (datos.gob.ar / Subsecretaría de Presupuesto)

Ejecución:
  python -m scripts.sync_modificaciones
"""
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.session import SessionLocal, engine
from app.database import models

# ── URLs de crédito anual por ejercicio ──────────────────
URLS = {
    2024: "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2024/credito-anual-2024.zip",
    2025: "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2025/credito-anual-2025.zip",
}

MAPA = {
    "jurisdiccion_id": "jurisdiccion_id",
    "servicio_id": "entidad_id",
    "programa_id": "programa_id",
    "inciso_id": "inciso_id",
    "principal_id": "principal_id",
    "parcial_id": "parcial_id",
    "fuente_id": "fuente_financiamiento_id",
    "credito_presupuestado": "monto_original",
    "credito_vigente": "monto_vigente",
    # fallbacks
    "jur_id": "jurisdiccion_id",
    "prg_id": "programa_id",
    "inc_id": "inciso_id",
    "ppa_id": "principal_id",
    "ff_id": "fuente_financiamiento_id",
}


def _descargar_df(anio: int) -> pd.DataFrame | None:
    url = URLS[anio]
    print(f"⬇️  Descargando crédito {anio}...")
    try:
        r = requests.get(url, timeout=180, stream=True)
        r.raise_for_status()
        content = b"".join(r.iter_content(65536))
        print(f"   OK ({len(content)/1_048_576:.1f} MB)")
    except Exception as e:
        print(f"   ❌ {e}")
        return None

    with zipfile.ZipFile(io.BytesIO(content)) as z:
        csv_name = next((n for n in z.namelist() if n.endswith(".csv")), None)
        if not csv_name:
            return None
        z.extract(csv_name, "data/")
        path = f"data/{csv_name}"

    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue

    df.columns = [c.strip().lower() for c in df.columns]
    df = df.rename(columns=MAPA)

    # Filtrar el año correcto
    for col in ("ejercicio_presupuestario", "impacto_presupuestario_anio", "ejercicio"):
        if col in df.columns:
            df = df[df[col].astype(str).str.strip() == str(anio)]
            break

    for col in ("monto_original", "monto_vigente"):
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace(",", ".", regex=False)
                .str.replace(r"[^\d\.]", "", regex=True)
                .pipe(pd.to_numeric, errors="coerce")
                .fillna(0.0)
            )
    return df


def calcular_y_cargar_modificaciones():
    """
    Cruza crédito 2023 (base) vs crédito 2024/2025 (vigente)
    y persiste las diferencias como ModificacionPresupuestaria.
    """
    models.Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    # Cargar base 2023 desde la DB
    print("📖 Leyendo base 2023 desde la DB...")
    base_rows = db.query(models.PresupuestoBase).all()
    base_map = {}
    for r in base_rows:
        key = (r.programa_id, r.inciso_id, r.fuente_financiamiento_id)
        base_map[key] = r

    print(f"   {len(base_map):,} partidas base cargadas")

    total_insertadas = 0

    for anio, norma_id_prefix in [(2024, "CREDITO-VIGENTE-2024"), (2025, "CREDITO-VIGENTE-2025")]:
        df = _descargar_df(anio)
        if df is None or df.empty:
            continue

        # Dedup: borrar modificaciones previas de este año
        db.query(models.ModificacionPresupuestaria).filter(
            models.ModificacionPresupuestaria.norma_id.like(f"{norma_id_prefix}%")
        ).delete(synchronize_session=False)
        db.commit()

        # Agrupar por partida y sumar crédito vigente
        group_cols = [c for c in ["programa_id", "inciso_id", "fuente_financiamiento_id"] if c in df.columns]
        if not group_cols:
            continue

        df_agg = df.groupby(group_cols, as_index=False)["monto_vigente"].sum()
        insertadas = 0

        for _, row in df_agg.iterrows():
            prog_id = str(row.get("programa_id", "")).strip()
            inciso_id = str(row.get("inciso_id", "")).strip()
            ff_id = str(row.get("fuente_financiamiento_id", "")).strip()
            monto_vigente_nuevo = float(row.get("monto_vigente", 0) or 0)

            if not prog_id:
                continue

            key = (prog_id, inciso_id, ff_id)
            base = base_map.get(key)
            monto_base = base.monto_vigente if base else 0.0

            # La "modificación" es la diferencia entre el crédito vigente del año nuevo
            # y el crédito vigente de la base 2023
            diferencia = monto_vigente_nuevo - monto_base
            if diferencia == 0:
                continue

            aumento = max(0, diferencia)
            reduccion = max(0, -diferencia)

            mod = models.ModificacionPresupuestaria(
                norma_id=f"{norma_id_prefix}-{prog_id}-{inciso_id}",
                fecha_boletin=None,
                programa_id=prog_id,
                inciso_id=inciso_id if inciso_id != "nan" else None,
                principal_id=None,
                aumento=aumento,
                reduccion=reduccion,
                monto_neto=diferencia,
            )
            db.add(mod)
            insertadas += 1

            if insertadas % 5000 == 0:
                db.commit()
                print(f"   ... {insertadas:,} modificaciones")

        db.commit()
        total_insertadas += insertadas
        print(f"✅ {anio}: {insertadas:,} modificaciones de partida calculadas")

    db.close()
    print(f"\n🎯 Total: {total_insertadas:,} modificaciones cargadas")
    print("Ahora /api/v1/analisis/ranking mostrará ajustes reales vs base 2023")


if __name__ == "__main__":
    calcular_y_cargar_modificaciones()