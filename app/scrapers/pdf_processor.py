# app/scrapers/pdf_processor.py
"""
Extractor de tablas de anexos presupuestarios (Decisiones Administrativas / Decretos).
Las tablas siguen la clasificación del Clasificador Presupuestario del Sector Público Nacional.

Columnas esperadas en los anexos del BORA:
  JUR | ENT | PRG | SPG | PRY | ACT | OBR | INC | PPA | PAR | SPP | FF | UG | REDUCCION | AUMENTO
"""
import re
import camelot
import pdfplumber
import pandas as pd
from pathlib import Path


# ─── Mapeo de columnas del BORA al modelo ────────────────────────

COLUMNAS_MAPA = {
    # Alias del BORA → nombre normalizado
    "jur": "jurisdiccion_id",
    "jurisdiccion": "jurisdiccion_id",
    "ent": "entidad_id",
    "saf": "entidad_id",
    "prg": "programa_id",
    "spg": "subprograma_id",
    "pry": "proyecto_id",
    "act": "actividad_id",
    "obr": "obra_id",
    "inc": "inciso_id",
    "ppa": "principal_id",
    "par": "parcial_id",
    "spp": "subparcial_id",
    "ff": "fuente_financiamiento_id",
    "ug": "ubicacion_geografica_id",
    "reducción": "reduccion",
    "reduccion": "reduccion",
    "aumento": "aumento",
    "incremento": "aumento",
    "ampliación": "aumento",
}

COLUMNAS_NUMERICAS = ["reduccion", "aumento"]


def _normalizar_nombre(col: str) -> str:
    col = col.strip().lower().replace("\n", " ").replace(".", "")
    return COLUMNAS_MAPA.get(col, col)


def _parsear_monto(valor) -> float:
    """Convierte strings con puntos/comas de miles a float."""
    if pd.isna(valor) or str(valor).strip() in ("", "-", "—"):
        return 0.0
    s = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
    s = re.sub(r"[^\d\.]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def extraer_tabla_presupuesto(pdf_path: str) -> pd.DataFrame | None:
    """
    Estrategia 1 (principal): Camelot con lattice para tablas con líneas visibles.
    Estrategia 2 (fallback): pdfplumber para tablas sin bordes.
    Retorna DataFrame con columnas normalizadas o None.
    """
    path = Path(pdf_path)
    if not path.exists():
        print(f"❌ Archivo no encontrado: {pdf_path}")
        return None

    df = _intentar_camelot(pdf_path)
    if df is None or df.empty:
        print("⚠️  Camelot no detectó tablas, intentando pdfplumber...")
        df = _intentar_pdfplumber(pdf_path)

    if df is None or df.empty:
        print(f"❌ No se pudo extraer tabla de {pdf_path}")
        return None

    df = _post_procesar(df)
    return df


def _intentar_camelot(pdf_path: str) -> pd.DataFrame | None:
    try:
        tablas_lattice = camelot.read_pdf(pdf_path, pages="all", flavor="lattice")
        tablas_stream = camelot.read_pdf(
            pdf_path, pages="all", flavor="stream",
            edge_tol=50, row_tol=5
        ) if len(tablas_lattice) == 0 else []

        tablas = tablas_lattice if len(tablas_lattice) > 0 else tablas_stream
        if len(tablas) == 0:
            return None

        frames = []
        for tabla in tablas:
            df = tabla.df.copy()
            if df.shape[0] < 2:
                continue
            # Primera fila = encabezado
            df.columns = [_normalizar_nombre(c) for c in df.iloc[0]]
            df = df[1:].reset_index(drop=True)
            # Filtrar filas completamente vacías
            df = df.replace("", pd.NA).dropna(how="all")
            frames.append(df)

        return pd.concat(frames, ignore_index=True) if frames else None

    except Exception as e:
        print(f"  Camelot error: {e}")
        return None


def _intentar_pdfplumber(pdf_path: str) -> pd.DataFrame | None:
    frames = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tablas = page.extract_tables()
                for tabla in tablas:
                    if not tabla or len(tabla) < 2:
                        continue
                    df = pd.DataFrame(tabla[1:], columns=[_normalizar_nombre(c) for c in tabla[0]])
                    frames.append(df)
    except Exception as e:
        print(f"  pdfplumber error: {e}")
        return None

    return pd.concat(frames, ignore_index=True) if frames else None


def _post_procesar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpieza final:
    - Renombra columnas al modelo
    - Convierte montos a float
    - Calcula monto_neto
    - Elimina filas sin programa_id
    """
    # Renombrar columnas ya mapeadas (segunda pasada por si quedaron alias)
    df = df.rename(columns=lambda c: COLUMNAS_MAPA.get(c.strip().lower(), c))

    # Convertir montos
    for col in COLUMNAS_NUMERICAS:
        if col in df.columns:
            df[col] = df[col].apply(_parsear_monto)
        else:
            df[col] = 0.0

    df["monto_neto"] = df["aumento"] - df["reduccion"]

    # Limpiar IDs
    for col in ["jurisdiccion_id", "programa_id", "inciso_id", "principal_id"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.zfill(
                {"jurisdiccion_id": 2, "programa_id": 2, "inciso_id": 1, "principal_id": 2}.get(col, 2)
            )

    # Filtrar filas sin programa
    if "programa_id" in df.columns:
        df = df[df["programa_id"].notna() & (df["programa_id"] != "nan")]

    return df.reset_index(drop=True)