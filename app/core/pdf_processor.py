# app/core/pdf_processor.py
"""
Extractor de tablas de modificaciones presupuestarias desde PDFs de Infoleg/BORA.

Las Planillas Anexas de las DAs del JGM tienen formato tabular con columnas:
    Jurisdicción | SAF | Programa | SubP | Proyecto | Act | Obra |
    Fuente Financiamiento | Inciso | Principal | Parcial | Aumento | Disminución

Estrategia de extracción (en orden de preferencia):
  1. pdfplumber  — mejor para PDFs nativos con texto embebido (mayoría 2023-2026)
  2. camelot     — fallback para tablas con bordes explícitos
  3. regex       — fallback de último recurso sobre texto plano

Compatibilidad con daily_sync.py:
  - extraer_modificaciones_pdf(pdf_path) → list[dict]  (función principal)
  - extraer_tabla_presupuesto(pdf_path)  → alias que retorna DataFrame
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Columnas esperadas en las planillas anexas ────────────────────────────────

_COL_ALIASES: dict[str, list[str]] = {
    "jurisdiccion":  ["jurisdicción", "jurisdiccion", "jur", "cod.jur", "cod jur"],
    "saf":           ["saf", "s.a.f.", "entidad", "ent", "cod.ent"],
    "programa":      ["programa", "prog", "prog.", "cod.prog"],
    "subprograma":   ["subprograma", "subp", "sub-p", "subp."],
    "proyecto":      ["proyecto", "proy", "proy."],
    "actividad":     ["actividad", "act", "act."],
    "obra":          ["obra"],
    "fuente":        ["fuente", "ff", "f.f.", "fuente de financiamiento", "fte"],
    "inciso":        ["inciso", "inc", "inc."],
    "principal":     ["principal", "p.p.", "pp", "p. principal", "pppal"],
    "parcial":       ["parcial", "parc", "parc.", "p. parcial"],
    "aumento":       ["aumento", "aum", "ampliación", "ampliacion", "incremento",
                      "credito a aumentar", "aumentos", "a aumentar"],
    "disminucion":   ["disminución", "disminucion", "dis", "reducción", "reduccion",
                      "credito a reducir", "disminuciones", "a reducir", "reducir"],
}

_MONTO_MINIMO = 1_000.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalizar_texto(s: str) -> str:
    return re.sub(r"[\s\.\-]+", " ", s.lower()).strip()


def _normalizar_col(nombre: str) -> Optional[str]:
    """
    Devuelve el nombre canónico de la columna.
    Usa matching exacto o de prefijo/sufijo para aliases largos (≥4 chars).
    Para aliases cortos (≤3 chars) exige matching exacto para evitar falsos positivos.
    """
    n = _normalizar_texto(nombre)
    for canon, aliases in _COL_ALIASES.items():
        for alias in aliases:
            a = _normalizar_texto(alias)
            if len(a) <= 3:
                # Alias corto (ej: "ent", "saf", "ff") → solo matching exacto
                if n == a:
                    return canon
            else:
                # Alias largo → matching por contenido (más flexible)
                if a == n or a in n or n in a:
                    return canon
    return None


def _limpiar_monto(s: str) -> float:
    if not s:
        return 0.0
    s = str(s).strip().replace(" ", "").replace("\xa0", "").replace("$", "")
    if s in ("-", "—", "–", "0", ""):
        return 0.0
    # Formato argentino: punto miles + coma decimal → '1.234.567,89'
    if re.search(r"\d\.\d{3}", s) and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    elif re.search(r"\d\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        return abs(float(s))
    except ValueError:
        return 0.0


def _detectar_mapa_columnas(fila: list[str]) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for i, celda in enumerate(fila):
        if not celda:
            continue
        canon = _normalizar_col(str(celda))
        if canon and canon not in mapa:
            mapa[canon] = i
    return mapa


def _parsear_fila(fila: list[str], mapa_cols: dict[str, int]) -> Optional[dict]:
    def get(canon: str) -> str:
        idx = mapa_cols.get(canon)
        if idx is None or idx >= len(fila):
            return ""
        return str(fila[idx]).strip()

    jur = get("jurisdiccion").lstrip("0") or get("jurisdiccion")
    if not jur or not re.match(r"^\d{1,3}$", jur.strip()):
        return None

    aumento = _limpiar_monto(get("aumento"))
    disminucion = _limpiar_monto(get("disminucion"))

    if aumento + disminucion < _MONTO_MINIMO:
        return None

    def zpad(val: str, n: int) -> Optional[str]:
        v = val.strip()
        return v.zfill(n) if re.match(r"^\d+$", v) else (v or None)

    return {
        "jurisdiccion_id": zpad(jur, 2),
        "saf_id":          zpad(get("saf"), 3),
        "programa_id":     zpad(get("programa"), 2),
        "subprograma_id":  zpad(get("subprograma"), 2) if get("subprograma") else None,
        "proyecto_id":     zpad(get("proyecto"), 2) if get("proyecto") else None,
        "actividad_id":    zpad(get("actividad"), 2) if get("actividad") else None,
        "obra_id":         zpad(get("obra"), 2) if get("obra") else None,
        "fuente_id":       zpad(get("fuente"), 2),
        "inciso_id":       get("inciso").strip() or None,
        "principal_id":    get("principal").strip() or None,
        "parcial_id":      get("parcial").strip() or None,
        "aumento":         aumento,
        "reduccion":       disminucion,   # alias para compatibilidad con daily_sync
        "disminucion":     disminucion,
        "monto_neto":      aumento - disminucion,
    }


# ── Estrategia 1: pdfplumber ──────────────────────────────────────────────────

def _extraer_con_pdfplumber(pdf_path: str) -> list[dict]:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber no instalado: pip install pdfplumber")
        return []

    filas_extraidas: list[dict] = []
    configs = [
        {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict",
         "snap_tolerance": 5, "join_tolerance": 3, "edge_min_length": 20},
        {"vertical_strategy": "lines", "horizontal_strategy": "lines",
         "snap_tolerance": 5, "join_tolerance": 3},
        {"vertical_strategy": "text", "horizontal_strategy": "text",
         "snap_tolerance": 6, "join_tolerance": 4},
    ]

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pagina in pdf.pages:
                for config in configs:
                    tablas = pagina.extract_tables(config)
                    if not tablas:
                        continue
                    for tabla in tablas:
                        if not tabla or len(tabla) < 2:
                            continue
                        mapa_cols: dict[str, int] = {}
                        inicio_datos = 0
                        for i, fila in enumerate(tabla[:6]):
                            mapa = _detectar_mapa_columnas([c or "" for c in fila])
                            if "jurisdiccion" in mapa and (
                                "aumento" in mapa or "disminucion" in mapa
                            ):
                                mapa_cols = mapa
                                inicio_datos = i + 1
                                break
                        if not mapa_cols:
                            continue
                        for fila in tabla[inicio_datos:]:
                            if fila is None:
                                continue
                            resultado = _parsear_fila([c or "" for c in fila], mapa_cols)
                            if resultado:
                                filas_extraidas.append(resultado)
                    if filas_extraidas:
                        break
    except Exception as e:
        logger.warning("pdfplumber error en %s: %s", pdf_path, e)
        return []

    logger.info("pdfplumber: %d filas de %s", len(filas_extraidas), pdf_path)
    return filas_extraidas


# ── Estrategia 2: camelot ─────────────────────────────────────────────────────

def _extraer_con_camelot(pdf_path: str) -> list[dict]:
    try:
        import camelot
    except ImportError:
        logger.warning("camelot no instalado: pip install camelot-py[cv]")
        return []

    filas_extraidas: list[dict] = []

    for flavor in ("lattice", "stream"):
        try:
            tablas = camelot.read_pdf(pdf_path, pages="all", flavor=flavor)
            if not tablas:
                continue
            for tabla in tablas:
                df = tabla.df
                if df.empty or len(df) < 2:
                    continue
                mapa_cols: dict[str, int] = {}
                inicio_datos = 0
                for i in range(min(6, len(df))):
                    mapa = _detectar_mapa_columnas(list(df.iloc[i].fillna("")))
                    if "jurisdiccion" in mapa and (
                        "aumento" in mapa or "disminucion" in mapa
                    ):
                        mapa_cols = mapa
                        inicio_datos = i + 1
                        break
                if not mapa_cols:
                    continue
                for _, row in df.iloc[inicio_datos:].iterrows():
                    resultado = _parsear_fila(
                        [str(v) for v in row.fillna("")], mapa_cols
                    )
                    if resultado:
                        filas_extraidas.append(resultado)
            if filas_extraidas:
                logger.info("camelot (%s): %d filas de %s", flavor, len(filas_extraidas), pdf_path)
                return filas_extraidas
        except Exception as e:
            logger.debug("camelot %s falló en %s: %s", flavor, pdf_path, e)

    return filas_extraidas


# ── Estrategia 3: regex sobre texto plano ─────────────────────────────────────

_RE_FILA_PRESUP = re.compile(
    r"^\s*"
    r"(?P<jur>\d{2,3})\s+"
    r"(?P<saf>\d{3,5})\s+"
    r"(?P<prog>\d{1,3})\s+"
    r"(?P<subp>\d{1,3})\s+"
    r"(?P<proy>\d{1,3})\s+"
    r"(?P<act>\d{1,3})\s+"
    r"(?P<obra>\d{1,3})\s+"
    r"(?P<ff>\d{2})\s+"
    r"(?P<inc>\d{1})\s+"
    r"(?P<ppal>\d{1,3})\s+"
    r"(?P<parc>\d{0,3})\s*"
    r"(?P<aum>[\d.,]+)\s+"
    r"(?P<dis>[\d.,]+)"
    r"\s*$",
    re.MULTILINE,
)


def _extraer_con_regex(pdf_path: str) -> list[dict]:
    texto_completo = ""
    for modulo in ("pypdf", "PyPDF2"):
        try:
            lib = __import__(modulo)
            with open(pdf_path, "rb") as f:
                reader = lib.PdfReader(f)
                for pagina in reader.pages:
                    texto_completo += (pagina.extract_text() or "") + "\n"
            break
        except ImportError:
            continue
        except Exception as e:
            logger.debug("Error con %s en %s: %s", modulo, pdf_path, e)

    if not texto_completo.strip():
        logger.warning("No se pudo extraer texto de %s", pdf_path)
        return []

    filas: list[dict] = []
    for m in _RE_FILA_PRESUP.finditer(texto_completo):
        aumento = _limpiar_monto(m.group("aum"))
        disminucion = _limpiar_monto(m.group("dis"))
        if aumento + disminucion < _MONTO_MINIMO:
            continue
        filas.append({
            "jurisdiccion_id": m.group("jur").zfill(2),
            "saf_id":          m.group("saf"),
            "programa_id":     m.group("prog").zfill(2),
            "subprograma_id":  m.group("subp") or None,
            "proyecto_id":     m.group("proy") or None,
            "actividad_id":    m.group("act") or None,
            "obra_id":         m.group("obra") or None,
            "fuente_id":       m.group("ff"),
            "inciso_id":       m.group("inc"),
            "principal_id":    m.group("ppal") or None,
            "parcial_id":      m.group("parc") or None,
            "aumento":         aumento,
            "reduccion":       disminucion,
            "disminucion":     disminucion,
            "monto_neto":      aumento - disminucion,
        })

    logger.info("regex: %d filas de %s", len(filas), pdf_path)
    return filas


# ── API pública ───────────────────────────────────────────────────────────────

def extraer_modificaciones_pdf(pdf_path: str) -> list[dict]:
    """
    Extrae filas de modificaciones presupuestarias de un PDF de DA.
    Prueba: pdfplumber → camelot → regex.
    Retorna lista de dicts compatibles con ModificacionPresupuestaria.
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF no encontrado: %s", pdf_path)
        return []
    if path.stat().st_size < 500:
        logger.warning("PDF sospechosamente pequeño (%d bytes): %s", path.stat().st_size, pdf_path)
        return []

    filas = _extraer_con_pdfplumber(pdf_path)
    if filas:
        return filas

    logger.info("pdfplumber sin resultados — probando camelot...")
    filas = _extraer_con_camelot(pdf_path)
    if filas:
        return filas

    logger.info("camelot sin resultados — probando regex...")
    filas = _extraer_con_regex(pdf_path)
    if filas:
        return filas

    logger.warning("Ninguna estrategia extrajo datos de %s", pdf_path)
    return []


def extraer_tabla_presupuesto(pdf_path: str):
    """
    Alias de compatibilidad con daily_sync.py.
    Retorna un DataFrame de pandas, o None si no hay datos.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas no instalado")
        return None
    filas = extraer_modificaciones_pdf(pdf_path)
    if not filas:
        return None
    return pd.DataFrame(filas)


# ── Diagnóstico / test manual ─────────────────────────────────────────────────

def diagnosticar_pdf(pdf_path: str) -> None:
    """
    Imprime info de diagnóstico sobre un PDF.
    Uso: python -m app.core.pdf_processor diagnosticar <ruta>
    """
    path = Path(pdf_path)
    print(f"\n{'='*60}")
    print(f"PDF: {path.name}  ({path.stat().st_size:,} bytes)")
    print(f"{'='*60}")
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            print(f"Páginas: {len(pdf.pages)}")
            for i, pag in enumerate(pdf.pages[:3], 1):
                tablas = pag.extract_tables()
                print(f"\n  Página {i}: {len(tablas)} tabla(s)")
                for j, tabla in enumerate(tablas[:2], 1):
                    if tabla:
                        print(f"    Tabla {j}: {len(tabla)} filas × {len(tabla[0])} cols")
                        print(f"    Encabezado: {tabla[0][:8]}")
                        if len(tabla) > 1:
                            print(f"    Fila[1]:    {tabla[1][:8]}")
    except ImportError:
        print("pdfplumber no disponible")

    print("\n--- Extracción automática ---")
    filas = extraer_modificaciones_pdf(pdf_path)
    print(f"Resultado: {len(filas)} filas extraídas")
    if filas:
        print(f"Primera fila: {filas[0]}")
        total_aum = sum(f["aumento"] for f in filas)
        total_dis = sum(f["disminucion"] for f in filas)
        print(f"Total aumentos:      ${total_aum:>20,.2f}")
        print(f"Total disminuciones: ${total_dis:>20,.2f}")
        print(f"Neto:                ${total_aum - total_dis:>20,.2f}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("Uso: python -m app.core.pdf_processor <ruta_al_pdf>")
        print("     python -m app.core.pdf_processor diagnosticar <ruta_al_pdf>")
        sys.exit(1)
    if sys.argv[1] == "diagnosticar" and len(sys.argv) >= 3:
        diagnosticar_pdf(sys.argv[2])
    else:
        filas = extraer_modificaciones_pdf(sys.argv[1])
        print(f"\n{len(filas)} filas extraídas")
        for f in filas[:10]:
            print(f)