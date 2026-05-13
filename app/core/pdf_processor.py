# app/core/pdf_processor.py
"""
Extractor de modificaciones presupuestarias desde PDFs de DAs del JGM (BORA/Infoleg).

Los Anexos de las DAs tienen DOS formatos según el artículo al que pertenecen:

  FORMATO A — Artículo 1° (modificación de créditos):
    Layout jerárquico por página. Encabezado contiene Jurisdicción / Entidad / Programa.
    La columna IMPORTE EN $ al final de cada línea puede ser positiva (aumento)
    o negativa (disminución). El importe relevante es el de la línea "TOTAL PROGRAMA"
    (o "TOTAL ENTIDAD" / "TOTAL GASTOS CORRIENTES Y DE CAPITAL").

    Ejemplo de encabezado:
      Jurisdicción : 30 Ministerio del Interior
      Entidad      : 325 Ministerio del Interior        ← solo si es ente descentralizado
      Programa     : 19 Relaciones con las Provincias
      Sub-Programa : 0
      Proyecto     : 0

  FORMATO B — Artículo 2° (reprogramación de ejecución):
    Layout similar pero la clave útil es "TOTAL SERVICIO" al final de cada página,
    con Jurisdicción y Servicio (SAF) en el encabezado.

Estrategia:
  1. pdfplumber sobre texto plano (funciona bien en ambos formatos, es el estándar)
  2. regex de fallback sobre el mismo texto (sin tablas)

Compatibilidad con daily_sync.py:
  - extraer_modificaciones_pdf(pdf_path) → list[dict]
  - extraer_tabla_presupuesto(pdf_path)  → DataFrame | None
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_MONTO_MINIMO = 1_000.0

# Líneas de total que capturamos por página (tomamos la primera que aparezca)
_TOTALES = [
    "TOTAL PROGRAMA",
    "TOTAL ENTIDAD",
    "TOTAL GASTOS CORRIENTES Y DE CAPITAL",
    "TOTAL SERVICIO",           # formato art. 2°
    "TOTAL APLICACIONES FINANCIERAS",
    "TOTAL CONTRIBUCIONES FIGURATIVAS",
    "TOTAL RECURSOS CORRIENTES Y DE CAPITAL",
    "TOTAL GASTOS FIGURATIVOS",
    "TOTAL FUENTES FINANCIERAS",
]

# Tipos de página que NO queremos (no son modificaciones de gastos reales)
_SKIP_TIPOS = [
    "APLICACIONES FINANCIERAS",
    "CONTRIBUCIONES FIGURATIVAS",
    "RECURSOS CORRIENTES",
    "FUENTES FINANCIERAS",
    "GASTOS FIGURATIVOS",
    "REPROGRAMACION",           # art. 2° — cuotas, no créditos
]

# Regex para extraer número + nombre de una línea de encabezado
# Ej: "Jurisdicción : 30Ministerio del Interior" o "Jurisdicción : 30 Ministerio..."
_RE_HEADER = re.compile(
    r"(?P<tipo>Jurisdicci[oó]n|Sub-Jurisdicci[oó]n|Entidad|"
    r"Programa|Sub-Programa|Proyecto|"
    r"JURISDICCI[OÓ]N|SERVICIO)\s*[:\s]+(\d+)\s*(.*)",
    re.IGNORECASE,
)

_RE_MONTO = re.compile(r"([-]?[\d.,]+(?:\.\d{3})*(?:,\d+)?)\s*$")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _limpiar_monto(s: str) -> float:
    """Convierte '10.000.000.000' o '-10.000.000.000' o '1.234,56' → float."""
    s = s.strip().replace(" ", "").replace("\xa0", "").replace("$", "")
    if not s or s in ("-", "—", "–"):
        return 0.0
    negativo = s.startswith("-")
    s = s.lstrip("-")
    # Formato argentino: punto de miles + coma decimal → '1.234.567,89'
    if re.search(r"\d\.\d{3}", s) and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    elif re.search(r"\d\.\d{3}", s):
        s = s.replace(".", "")
    try:
        val = float(s)
        return -val if negativo else val
    except ValueError:
        return 0.0


def _extraer_monto_linea(linea: str) -> Optional[float]:
    """Extrae el último número de una línea (el importe)."""
    # Buscar un número al final de la línea (con o sin signo)
    m = re.search(r"(-?[\d.]+(?:,\d+)?)\s*$", linea.strip())
    if m:
        val = _limpiar_monto(m.group(1))
        if abs(val) >= _MONTO_MINIMO:
            return val
    return None


def _es_pagina_skip(texto: str) -> bool:
    """True si la página es de aplicaciones financieras, cuotas, etc. (no nos interesa)."""
    primeras = texto[:400].upper()
    return any(skip in primeras for skip in _SKIP_TIPOS)


def _zpad(val: str, n: int) -> Optional[str]:
    v = val.strip()
    return v.zfill(n) if re.match(r"^\d+$", v) else (v or None)


# ── Parser principal por página ───────────────────────────────────────────────

def _parsear_pagina(texto: str) -> Optional[dict]:
    """
    Parsea una página del PDF y devuelve un dict con la modificación,
    o None si la página no contiene datos relevantes.
    """
    if not texto or not texto.strip():
        return None

    if _es_pagina_skip(texto):
        return None

    lineas = texto.splitlines()

    # ── Extraer metadatos del encabezado ─────────────────────────────────────
    jur_id: Optional[str] = None
    saf_id: Optional[str] = None
    prog_id: Optional[str] = None
    subprog_id: Optional[str] = None
    proy_id: Optional[str] = None

    for linea in lineas[:30]:
        linea_s = linea.strip()
        m = _RE_HEADER.match(linea_s)
        if not m:
            continue
        tipo   = m.group("tipo").upper()
        codigo = m.group(2).strip()
        if "JURISDICCI" in tipo and "SUB" not in tipo:
            jur_id = codigo
        elif "SUB-JURISDICCI" in tipo or "SUBJURISDICCI" in tipo:
            pass  # ignorar
        elif "ENTIDAD" in tipo or "SERVICIO" in tipo:
            saf_id = codigo
        elif "SUB-PROGRAMA" in tipo or "SUBPROGRAMA" in tipo:
            subprog_id = codigo if codigo != "0" else None
        elif "PROYECTO" in tipo:
            proy_id = codigo if codigo != "0" else None
        elif "PROGRAMA" in tipo:
            prog_id = codigo

    if not jur_id:
        return None

    # ── Extraer el importe de la línea TOTAL relevante ────────────────────────
    monto: Optional[float] = None
    total_label_usado: Optional[str] = None

    for linea in lineas:
        linea_up = linea.upper().strip()
        for label in _TOTALES:
            if linea_up.startswith(label):
                val = _extraer_monto_linea(linea)
                if val is not None and abs(val) >= _MONTO_MINIMO:
                    monto = val
                    total_label_usado = label
                    break
        if monto is not None:
            break

    if monto is None:
        return None

    # ── Determinar aumento / disminución ──────────────────────────────────────
    aumento    = monto if monto > 0 else 0.0
    disminucion = abs(monto) if monto < 0 else 0.0

    return {
        "jurisdiccion_id": _zpad(jur_id, 2),
        "saf_id":          _zpad(saf_id, 3) if saf_id else None,
        "programa_id":     _zpad(prog_id, 2) if prog_id else None,
        "subprograma_id":  _zpad(subprog_id, 2) if subprog_id else None,
        "proyecto_id":     _zpad(proy_id, 2) if proy_id else None,
        "actividad_id":    None,
        "obra_id":         None,
        "fuente_id":       None,
        "inciso_id":       None,
        "principal_id":    None,
        "parcial_id":      None,
        "aumento":         round(aumento, 2),
        "reduccion":       round(disminucion, 2),
        "disminucion":     round(disminucion, 2),
        "monto_neto":      round(monto, 2),
        "total_label":     total_label_usado,
    }


# ── Estrategia 1: pdfplumber ──────────────────────────────────────────────────

def _extraer_con_pdfplumber(pdf_path: str) -> list[dict]:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber no instalado: pip install pdfplumber")
        return []

    resultados: list[dict] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text() or ""
                fila = _parsear_pagina(texto)
                if fila:
                    resultados.append(fila)
    except Exception as e:
        logger.warning("pdfplumber error en %s: %s", pdf_path, e)
        return []

    logger.info("pdfplumber: %d filas de %s", len(resultados), pdf_path)
    return resultados


# ── Estrategia 2: regex sobre texto plano (fallback) ─────────────────────────

def _extraer_con_regex(pdf_path: str) -> list[dict]:
    """
    Extrae texto con pypdf/PyPDF2 y aplica el mismo parser por 'página'.
    Útil si pdfplumber falla en PDFs escaneados o raros.
    """
    texto_completo = ""
    for modulo in ("pypdf", "PyPDF2"):
        try:
            lib = __import__(modulo)
            with open(pdf_path, "rb") as f:
                reader = lib.PdfReader(f)
                paginas = []
                for pag in reader.pages:
                    paginas.append(pag.extract_text() or "")
            # Procesar página por página
            resultados = []
            for texto in paginas:
                fila = _parsear_pagina(texto)
                if fila:
                    resultados.append(fila)
            if resultados:
                logger.info("regex/pypdf: %d filas de %s", len(resultados), pdf_path)
                return resultados
        except ImportError:
            continue
        except Exception as e:
            logger.debug("Error con %s en %s: %s", modulo, pdf_path, e)

    return []


# ── Deduplicación y consolidación ─────────────────────────────────────────────

def _consolidar(filas: list[dict]) -> list[dict]:
    """
    Consolida filas con la misma clave (jur + prog + subprog).
    Cuando una DA tiene múltiples páginas para el mismo programa
    (ej: una por FF 11, otra por FF 13), suma los montos.
    """
    acum: dict[tuple, dict] = {}
    for f in filas:
        key = (
            f.get("jurisdiccion_id"),
            f.get("saf_id"),
            f.get("programa_id"),
            f.get("subprograma_id"),
        )
        if key not in acum:
            acum[key] = dict(f)
        else:
            acum[key]["aumento"]    += f["aumento"]
            acum[key]["reduccion"]  += f["reduccion"]
            acum[key]["disminucion"] += f["disminucion"]
            acum[key]["monto_neto"] += f["monto_neto"]

    # Redondear
    resultado = []
    for f in acum.values():
        f["aumento"]    = round(f["aumento"], 2)
        f["reduccion"]  = round(f["reduccion"], 2)
        f["disminucion"] = round(f["disminucion"], 2)
        f["monto_neto"] = round(f["monto_neto"], 2)
        if abs(f["monto_neto"]) >= _MONTO_MINIMO:
            resultado.append(f)

    return sorted(resultado, key=lambda x: (
        x.get("jurisdiccion_id") or "",
        x.get("saf_id") or "",
        x.get("programa_id") or "",
    ))


# ── API pública ───────────────────────────────────────────────────────────────

def extraer_modificaciones_pdf(pdf_path: str) -> list[dict]:
    """
    Extrae filas de modificaciones presupuestarias de un PDF de DA.
    Prueba: pdfplumber → regex/pypdf.
    Retorna lista de dicts compatibles con ModificacionPresupuestaria.
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("PDF no encontrado: %s", pdf_path)
        return []
    if path.stat().st_size < 500:
        logger.warning("PDF sospechosamente pequeño (%d bytes): %s",
                       path.stat().st_size, pdf_path)
        return []

    filas = _extraer_con_pdfplumber(pdf_path)
    if not filas:
        logger.info("pdfplumber sin resultados — probando regex...")
        filas = _extraer_con_regex(pdf_path)

    if not filas:
        logger.warning("Ninguna estrategia extrajo datos de %s", pdf_path)
        return []

    return _consolidar(filas)


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
    print(f"\n{'='*70}")
    print(f"PDF: {path.name}  ({path.stat().st_size:,} bytes)")
    print(f"{'='*70}")

    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            print(f"Páginas: {len(pdf.pages)}")
            print(f"\n--- Muestra páginas 1-3 (texto crudo) ---")
            for i, pag in enumerate(pdf.pages[:3], 1):
                texto = pag.extract_text() or ""
                print(f"\n  [Pág {i}] {len(texto)} chars")
                # Mostrar solo encabezado y líneas TOTAL
                for linea in texto.splitlines():
                    if any(kw in linea.upper() for kw in
                           ["JURISDICCI", "ENTIDAD", "PROGRAMA", "SERVICIO",
                            "TOTAL", "ARTICULO"]):
                        print(f"    {linea.strip()}")
    except ImportError:
        print("pdfplumber no disponible — usando pypdf")

    print(f"\n--- Extracción automática ---")
    filas = extraer_modificaciones_pdf(pdf_path)
    print(f"Resultado: {len(filas)} filas extraídas")

    if filas:
        print(f"\n{'JUR':>4} {'SAF':>5} {'PROG':>5} {'SUBP':>5}  "
              f"{'AUMENTO':>20}  {'DISMINUCIÓN':>20}  {'NETO':>20}")
        print("-" * 85)
        for f in filas[:20]:
            print(
                f"  {f.get('jurisdiccion_id','?'):>4}"
                f"  {f.get('saf_id') or '':>5}"
                f"  {f.get('programa_id') or '':>5}"
                f"  {f.get('subprograma_id') or '':>5}"
                f"  {f['aumento']:>20,.0f}"
                f"  {f['disminucion']:>20,.0f}"
                f"  {f['monto_neto']:>20,.0f}"
            )
        if len(filas) > 20:
            print(f"  ... ({len(filas) - 20} filas más)")

        total_aum = sum(f["aumento"] for f in filas)
        total_dis = sum(f["disminucion"] for f in filas)
        print(f"\n{'─'*85}")
        print(f"  {'TOTAL':>17}  {total_aum:>20,.0f}  {total_dis:>20,.0f}  "
              f"{total_aum - total_dis:>20,.0f}")
    print(f"{'='*70}\n")


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
        for f in filas[:15]:
            print(f)