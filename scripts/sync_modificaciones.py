# scripts/sync_modificaciones.py
"""
Estrategia:
- Base: credito_original_2023 en pesos nominales (seed_2023 ya multiplicó ×1_000_000)
- Vigente: credito_vigente del año más reciente (2026 > 2025 > 2024), también en millones
  → se normaliza ×1_000_000 antes de comparar contra la base 2023
- ModificacionPresupuestaria guarda la diferencia NETA en pesos nominales

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

URLS = {
    2024: "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2024/credito-anual-2024.zip",
    2025: "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2025/credito-anual-2025.zip",
    2026: "https://dgsiaf-repo.mecon.gob.ar/repository/pa/datasets/2026/credito-anual-2026.zip",
}

MAPA = {
    "jurisdiccion_id": "jurisdiccion_id",
    "jurisdiccion_desc": "jurisdiccion_desc",
    "servicio_id": "entidad_id",
    "servicio_desc": "entidad_desc",
    "programa_id": "programa_id",
    "programa_desc": "programa_desc",
    "inciso_id": "inciso_id",
    "inciso_desc": "inciso_desc",
    "principal_id": "principal_id",
    "parcial_id": "parcial_id",
    "fuente_id": "fuente_financiamiento_id",
    "credito_presupuestado": "monto_original",
    "credito_vigente": "monto_vigente",
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
                # Todos los años (2024/2025/2026) vienen en millones → pesos nominales
                .mul(1_000_000)
                .round(0)
            )
    return df


def calcular_y_cargar_modificaciones():
    """
    1. Descarga vigente 2024, 2025 y 2026 (todos en millones → normaliza ×1_000_000)
    2. Para cada partida toma el vigente más reciente (2026 > 2025 > 2024)
    3. Diferencia = vigente_reciente_pesos - monto_vigente_2023_pesos
    4. El ZIP de 2026 se regenera diariamente → cada run refleja ejecución actualizada
    """
    models.Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    print("📖 Leyendo base 2023 desde la DB...")
    base_rows = db.query(models.PresupuestoBase).filter(
        models.PresupuestoBase.ejercicio == 2023
    ).all()
    # Sumar monto_vigente por key (igual que agrupamos 2024/2025/2026)
    # Los valores en DB están en millones → multiplicar ×1_000_000 para pesos nominales
    base_map = {}
    for r in base_rows:
        key = (r.jurisdiccion_id, r.programa_id, r.inciso_id, r.fuente_financiamiento_id)
        # monto_vigente 2023 ya está en pesos nominales (seed_2023 multiplicó ×1_000_000)
        base_map[key] = base_map.get(key, 0.0) + (r.monto_vigente or 0.0)
    print(f"   {len(base_map):,} partidas base 2023 (agregadas)")

    # Descargar 2024, 2025, 2026
    dfs = {}
    for anio in [2024, 2025, 2026]:
        df = _descargar_df(anio)
        if df is not None and not df.empty:
            dfs[anio] = df

    if not dfs:
        print("❌ No se pudo descargar ningún CSV")
        db.close()
        return

    # Construir mapa de vigente más reciente (2026 pisa 2025, 2025 pisa 2024)
    vigente_map = {}
    for anio in sorted(dfs.keys()):
        df = dfs[anio]
        group_cols = [c for c in ["jurisdiccion_id", "programa_id", "inciso_id", "fuente_financiamiento_id"] if c in df.columns]
        if not group_cols:
            continue

        agg = {"monto_vigente": "sum"}
        for desc_col in ["jurisdiccion_desc", "programa_desc"]:
            if desc_col in df.columns:
                agg[desc_col] = "first"

        df_agg = df.groupby(group_cols, as_index=False).agg(agg)

        for _, row in df_agg.iterrows():
            jur_id    = str(row.get("jurisdiccion_id", "")).strip()
            prog_id   = str(row.get("programa_id", "")).strip()
            inciso_id = str(row.get("inciso_id", "")).strip()
            ff_id     = str(row.get("fuente_financiamiento_id", "")).strip()
            if not prog_id:
                continue
            key = (jur_id, prog_id, inciso_id, ff_id)
            vigente_map[key] = {
                "vigente":           float(row.get("monto_vigente", 0) or 0),
                "anio":              anio,
                "jurisdiccion_desc": str(row.get("jurisdiccion_desc", "")),
                "programa_desc":     str(row.get("programa_desc", "")),
            }

    anios_en_mapa = set(v["anio"] for v in vigente_map.values())
    print(f"   {len(vigente_map):,} partidas con vigente reciente {sorted(anios_en_mapa)}")

    # Borrar modificaciones sintéticas anteriores
    eliminadas = db.query(models.ModificacionPresupuestaria).filter(
        models.ModificacionPresupuestaria.norma_id.like("CREDITO-VIGENTE-%")
    ).delete(synchronize_session=False)
    db.commit()
    if eliminadas:
        print(f"   🗑️  {eliminadas:,} modificaciones sintéticas previas eliminadas")

    insertadas = 0
    total_reduccion = 0.0
    total_aumento = 0.0

    for key, v in vigente_map.items():
        jur_id, prog_id, inciso_id, ff_id = key

        # base_map[key] es float en pesos (ya agregado y ×1_000_000)
        vigente_2023     = base_map.get(key, 0.0)
        # v["vigente"] también en pesos (_descargar_df multiplicó ×1_000_000)
        vigente_reciente = v["vigente"]

        diferencia = vigente_reciente - vigente_2023

        if abs(diferencia) < 1.0:  # umbral 1 peso
            continue

        aumento   = max(0.0,  diferencia)
        reduccion = max(0.0, -diferencia)

        mod = models.ModificacionPresupuestaria(
            norma_id=f"CREDITO-VIGENTE-{v['anio']}-{jur_id}-{prog_id}-{inciso_id}",
            fecha_boletin=None,
            programa_id=prog_id,
            inciso_id=inciso_id if inciso_id not in ("", "nan") else None,
            principal_id=None,
            aumento=aumento,
            reduccion=reduccion,
            monto_neto=diferencia,
        )
        db.add(mod)
        insertadas += 1
        total_reduccion += reduccion
        total_aumento   += aumento

        if insertadas % 5000 == 0:
            db.commit()
            print(f"   ... {insertadas:,}")

    db.commit()
    db.close()

    print(f"\n✅ {insertadas:,} modificaciones calculadas")
    print(f"   Reducciones totales: ${total_reduccion/1e12:,.2f} B ARS")
    print(f"   Aumentos totales:    ${total_aumento/1e12:,.2f} B ARS")
    print(f"   Años incluidos:      {sorted(anios_en_mapa)}")


if __name__ == "__main__":
    calcular_y_cargar_modificaciones()