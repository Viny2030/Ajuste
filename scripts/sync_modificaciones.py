# scripts/sync_modificaciones.py
"""
Estrategia corregida:
- Base: credito_original_2023  (monto_original en presupuesto_base)
- Vigente: credito_vigente del año más reciente disponible (2025 > 2024)
- La ModificacionPresupuestaria guarda la diferencia NETA entre vigente más
  reciente y el monto_vigente de 2023, de modo que:
    monto_vigente_efectivo = presupuesto_base.monto_vigente + sum(mod.monto_neto)
  sea igual al credito_vigente del año más reciente.

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
            )
    return df


def calcular_y_cargar_modificaciones():
    """
    Lógica corregida:
    1. Descarga vigente 2024 y 2025
    2. Para cada partida, toma el vigente más reciente disponible
    3. La modificación = vigente_reciente - monto_vigente_2023
       (así calcular_variacion_real suma correctamente)
    """
    models.Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()

    print("📖 Leyendo base 2023 desde la DB...")
    base_rows = db.query(models.PresupuestoBase).all()
    # key: (programa_id, inciso_id, ff_id) -> row
    base_map = {}
    for r in base_rows:
        key = (r.programa_id, r.inciso_id, r.fuente_financiamiento_id)
        base_map[key] = r
    print(f"   {len(base_map):,} partidas base 2023")

    # Descargar ambos años
    dfs = {}
    for anio in [2024, 2025]:
        df = _descargar_df(anio)
        if df is not None and not df.empty:
            dfs[anio] = df

    if not dfs:
        print("❌ No se pudo descargar ningún CSV")
        db.close()
        return

    # Construir mapa de vigente más reciente por partida
    # key: (programa_id, inciso_id, ff_id) -> {vigente, anio, jurisdiccion_desc, programa_desc}
    vigente_map = {}

    for anio in sorted(dfs.keys()):  # 2024 primero, 2025 pisa
        df = dfs[anio]
        group_cols = [c for c in ["programa_id", "inciso_id", "fuente_financiamiento_id"] if c in df.columns]
        if not group_cols:
            continue

        # Agrupar sumando monto_vigente por clave de partida
        agg = {"monto_vigente": "sum"}
        # También traer desc si existe
        for desc_col in ["jurisdiccion_desc", "programa_desc"]:
            if desc_col in df.columns:
                agg[desc_col] = "first"

        df_agg = df.groupby(group_cols, as_index=False).agg(agg)

        for _, row in df_agg.iterrows():
            prog_id = str(row.get("programa_id", "")).strip()
            inciso_id = str(row.get("inciso_id", "")).strip()
            ff_id = str(row.get("fuente_financiamiento_id", "")).strip()
            if not prog_id:
                continue
            key = (prog_id, inciso_id, ff_id)
            vigente_map[key] = {
                "vigente": float(row.get("monto_vigente", 0) or 0),
                "anio": anio,
                "jurisdiccion_desc": str(row.get("jurisdiccion_desc", "")),
                "programa_desc": str(row.get("programa_desc", "")),
            }

    print(f"   {len(vigente_map):,} partidas con vigente reciente (2024/2025)")

    # Borrar todas las modificaciones anteriores calculadas por este script
    db.query(models.ModificacionPresupuestaria).filter(
        models.ModificacionPresupuestaria.norma_id.like("CREDITO-VIGENTE-%")
    ).delete(synchronize_session=False)
    db.commit()

    insertadas = 0
    total_reduccion = 0.0
    total_aumento = 0.0

    for key, v in vigente_map.items():
        prog_id, inciso_id, ff_id = key
        base = base_map.get(key)

        # monto_vigente_2023: si existe en base usamos ese, sino 0
        vigente_2023 = base.monto_vigente if base else 0.0
        vigente_reciente = v["vigente"]

        # La modificación neta = diferencia entre vigente reciente y vigente 2023
        # Esto hace que: base.monto_vigente + mod.monto_neto = vigente_reciente
        diferencia = vigente_reciente - vigente_2023

        # Solo registrar si hay diferencia significativa
        if abs(diferencia) < 0.01:
            continue

        aumento = max(0.0, diferencia)
        reduccion = max(0.0, -diferencia)

        mod = models.ModificacionPresupuestaria(
            norma_id=f"CREDITO-VIGENTE-{v['anio']}-{prog_id}-{inciso_id}",
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
        total_aumento += aumento

        if insertadas % 5000 == 0:
            db.commit()
            print(f"   ... {insertadas:,}")

    db.commit()
    db.close()

    print(f"\n✅ {insertadas:,} modificaciones calculadas")
    print(f"   Reducciones totales: ${total_reduccion:,.0f}")
    print(f"   Aumentos totales:    ${total_aumento:,.0f}")
    print(f"\n⚠️  Recordá que calcular_variacion_real usa:")
    print(f"   monto_vigente_efectivo = base.monto_vigente + sum(mod.monto_neto)")
    print(f"   = vigente_2023 + (vigente_reciente - vigente_2023)")
    print(f"   = vigente_reciente  ✓")
    print(f"\nAhora /api/v1/analisis/ranking mostrará el ajuste real correcto.")


if __name__ == "__main__":
    calcular_y_cargar_modificaciones()