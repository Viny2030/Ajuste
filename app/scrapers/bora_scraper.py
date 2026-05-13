# app/scrapers/bora_scraper.py
"""
Scraper del Boletín Oficial de la República Argentina (BORA).

El BORA bloquea scraping HTML/Playwright, pero expone una API JSON interna
no documentada que sí responde correctamente. Esta API es la misma que usa
el buscador del sitio web en el frontend.

Endpoint principal:
  POST https://www.boletinoficial.gob.ar/normas/buscar
  Content-Type: application/json

Esta fuente es COMPLEMENTARIA a Infoleg (bora_discovery.py):
  - Infoleg: tiene el CSV completo pero se actualiza mensualmente
  - BORA API: tiene los últimos días/semanas en tiempo casi real
  - Ambas se fusionan en daily_sync.py para cobertura completa

Para bajar los PDFs de los Anexos, el flujo es:
  1. Buscar la norma → obtener numero_boletin + fecha
  2. GET /detalleAviso/primera/{id_aviso}/{fecha_boletin} → HTML con links a PDFs
  3. Descargar el PDF del Anexo (suele estar en infraestructura.gob.ar o en el
     mismo BORA como adjunto)

Uso:
  python -m app.scrapers.bora_scraper --desde 10/12/2023
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

BORA_BASE = "https://www.boletinoficial.gob.ar"
BORA_API_BUSCAR = f"{BORA_BASE}/normas/buscar"
BORA_API_AVISO  = f"{BORA_BASE}/normas/getAviso"

# Sección 1 = Legislación y Avisos Oficiales (donde van las DAs del JGM)
SECCION_PRIMERA = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9",
    "Origin": BORA_BASE,
    "Referer": f"{BORA_BASE}/busquedaAvanzada/normas",
}

# Términos de búsqueda para DAs presupuestarias
TERMINOS_PRESUP = [
    "Decisión Administrativa presupuesto",
    "Decisión Administrativa crédito presupuestario",
    "Decisión Administrativa modificación presupuestaria",
]

ORGANISMO_JGM = "Jefatura de Gabinete de Ministros"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _fecha_bora(d: date) -> str:
    """Formato que usa la API del BORA: YYYYMMDD"""
    return d.strftime("%Y%m%d")


def _fecha_iso(bora_str: str) -> Optional[str]:
    """Convierte YYYYMMDD → YYYY-MM-DD. Retorna None si falla."""
    try:
        return datetime.strptime(str(bora_str), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


VERBOS_REDUCCION  = ["suprim", "reduccion", "reducción", "reduzcanse", "disminuy", "eliminase"]
VERBOS_AMPLIACION = ["amplias", "ampliación", "ampliacion", "incremento", "autorizase", "refuerzo"]
VERBOS_REASIGN    = ["transfier", "reasign", "redistrib"]


def _detectar_tipo_accion(texto: str) -> str:
    t = texto.lower()
    if any(v in t for v in VERBOS_REDUCCION):  return "REDUCCION"
    if any(v in t for v in VERBOS_REASIGN):    return "REASIGNACION"
    if any(v in t for v in VERBOS_AMPLIACION): return "AMPLIACION"
    return "MODIFICACION"


def _es_presupuestaria(titulo: str, sumario: str = "") -> bool:
    texto = (titulo + " " + sumario).lower()
    keywords = [
        "presupuest", "crédito", "credito", "modificac",
        "distributivo", "reduc", "amplia", "reasign",
    ]
    return any(k in texto for k in keywords)


# ── API del BORA ──────────────────────────────────────────────────────────────

async def _buscar_en_bora(
    client: httpx.AsyncClient,
    termino: str,
    desde: date,
    hasta: date,
) -> list[dict]:
    """
    Llama a la API JSON del BORA y retorna los avisos encontrados.
    La API acepta paginación implícita — retorna hasta ~20 resultados por llamada.
    """
    payload = {
        "parametros": {
            "terminos": termino,
            "organismos": "",
            "rubros": "",
            "numeroBoletin": "",
            "seccion": str(SECCION_PRIMERA),
            "desde": _fecha_bora(desde),
            "hasta": _fecha_bora(hasta),
            "tipoPublicacion": "DA",   # DA = Decisión Administrativa
        }
    }

    try:
        resp = await client.post(
            BORA_API_BUSCAR,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # La API retorna {"avisos": [...]} o {"resultados": [...]}
        return data.get("avisos") or data.get("resultados") or []
    except httpx.HTTPStatusError as e:
        logger.warning("BORA API error %s para '%s': %s", e.response.status_code, termino, e)
        return []
    except Exception as e:
        logger.warning("BORA API excepción para '%s': %s", termino, e)
        return []


async def _obtener_pdf_url(
    client: httpx.AsyncClient,
    id_aviso: str,
    fecha_boletin: str,
) -> Optional[str]:
    """
    Intenta obtener la URL del PDF del Anexo de un aviso del BORA.
    Prueba varias estrategias de URL.
    """
    # Estrategia 1: endpoint getAviso → buscar link PDF en la respuesta
    try:
        resp = await client.get(
            f"{BORA_API_AVISO}/{id_aviso}/{fecha_boletin}",
            timeout=15,
        )
        if resp.status_code == 200:
            # Buscar URLs de PDF en el HTML/JSON devuelto
            texto = resp.text
            pdfs = re.findall(
                r'https?://[^\s"\'<>]+\.pdf',
                texto,
                re.IGNORECASE,
            )
            if pdfs:
                return pdfs[0]
    except Exception as e:
        logger.debug("getAviso falló para %s: %s", id_aviso, e)

    # Estrategia 2: URL de adjunto directo del BORA
    # Formato observado en producción:
    # https://www.boletinoficial.gob.ar/storage/normas/pdfs/{fecha}/{id}.pdf
    candidatos = [
        f"{BORA_BASE}/storage/normas/pdfs/{fecha_boletin}/{id_aviso}.pdf",
        f"{BORA_BASE}/pdf/{fecha_boletin}/{id_aviso}",
    ]
    for url in candidatos:
        try:
            r = await client.head(url, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                return url
        except Exception:
            continue

    return None


# ── Función principal ─────────────────────────────────────────────────────────

async def buscar_normas_bora(
    desde: str = "10/12/2023",
    hasta: Optional[str] = None,
) -> list[dict]:
    """
    Busca DAs presupuestarias del JGM en el BORA usando su API interna.

    Args:
        desde: Fecha inicial en formato DD/MM/YYYY (default: inicio gestión Milei)
        hasta: Fecha final en formato DD/MM/YYYY (default: hoy)

    Returns:
        Lista de dicts con el mismo schema que buscar_normas() de bora_discovery.py,
        para ser fusionada en daily_sync.py.
    """
    try:
        desde_dt = datetime.strptime(desde, "%d/%m/%Y").date()
    except ValueError:
        desde_dt = date(2023, 12, 10)

    hasta_dt = (
        datetime.strptime(hasta, "%d/%m/%Y").date()
        if hasta else date.today()
    )

    resultados: dict[str, dict] = {}  # norma_id → norma_data

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        # Buscar en ventanas de 90 días para no superar el límite de la API
        cursor = desde_dt
        while cursor <= hasta_dt:
            fin_ventana = min(cursor + timedelta(days=89), hasta_dt)

            for termino in TERMINOS_PRESUP:
                logger.info(
                    "BORA: buscando '%s' [%s → %s]",
                    termino, cursor, fin_ventana,
                )
                avisos = await _buscar_en_bora(client, termino, cursor, fin_ventana)

                for aviso in avisos:
                    # Extraer campos — la API puede retornar distintos schemas
                    titulo = (
                        aviso.get("titulo") or
                        aviso.get("denominacion") or
                        aviso.get("nombre") or ""
                    )
                    sumario = aviso.get("sumario") or aviso.get("texto") or ""
                    organismo = aviso.get("organismo") or aviso.get("reparticion") or ""

                    # Filtrar: solo DAs presupuestarias
                    if not _es_presupuestaria(titulo, sumario):
                        continue

                    # Extraer número y año de la DA
                    num_match = re.search(r"(\d+)[/\s](\d{4})", titulo + " " + sumario)
                    if not num_match:
                        num_match = re.search(r"N[°º]\s*(\d+)", titulo + " " + sumario)

                    numero = num_match.group(1) if num_match else aviso.get("numero", "0")
                    fecha_bora_str = str(aviso.get("fechaBoletin") or aviso.get("fecha") or "")
                    fecha_iso = _fecha_iso(fecha_bora_str) or str(cursor)
                    anio = fecha_iso[:4]

                    norma_id = f"DA-{numero}-{anio}"
                    if norma_id in resultados:
                        continue

                    id_aviso = str(aviso.get("id") or aviso.get("idAviso") or "")
                    url_bora = (
                        aviso.get("urlBora") or
                        aviso.get("url") or
                        f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_bora_str}"
                    )

                    resultados[norma_id] = {
                        "norma_id":       norma_id,
                        "tipo_norma":     "DA",
                        "numero":         numero,
                        "anio":           int(anio),
                        "fecha_boletin":  fecha_iso,
                        "numero_boletin": str(aviso.get("numeroBoletin") or ""),
                        "pagina_boletin": str(aviso.get("pagina") or ""),
                        "organismo":      organismo,
                        "titulo":         titulo,
                        "sumario":        sumario[:500],
                        "texto_resumido": sumario[:1000],
                        "tipo_accion":    _detectar_tipo_accion(titulo + " " + sumario),
                        "url_infoleg":    url_bora,  # unificado con schema de Infoleg
                        "url_bora":       url_bora,
                        "id_aviso_bora":  id_aviso,
                        "fecha_bora_str": fecha_bora_str,
                        "pdf_hash":       _sha256(norma_id),
                        "fuente":         "BORA_API",
                    }

                # Pausa cortés entre requests
                await _sleep(0.5)

            cursor = fin_ventana + timedelta(days=1)

    normas = sorted(resultados.values(), key=lambda x: x["fecha_boletin"])
    logger.info("BORA API: %d DAs presupuestarias encontradas", len(normas))
    return normas


async def descargar_pdf_bora(
    norma_data: dict,
    destino: str,
) -> Optional[str]:
    """
    Descarga el PDF del Anexo de una DA desde el BORA.
    Complementa a descargar_pdf_norma() de bora_discovery.py.

    Args:
        norma_data: dict con 'id_aviso_bora', 'fecha_bora_str', 'url_bora'
        destino: ruta local donde guardar el PDF

    Returns:
        Path al PDF descargado, o None si no se pudo.
    """
    destino_path = Path(destino)
    if destino_path.exists() and destino_path.stat().st_size > 500:
        return destino

    id_aviso = norma_data.get("id_aviso_bora", "")
    fecha_str = norma_data.get("fecha_bora_str", "")
    url_bora = norma_data.get("url_bora", "")

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        # Primero obtener URL exacta del PDF
        pdf_url = None
        if id_aviso and fecha_str:
            pdf_url = await _obtener_pdf_url(client, id_aviso, fecha_str)

        # Fallback: buscar link PDF en la página HTML del aviso
        if not pdf_url and url_bora:
            try:
                resp = await client.get(url_bora, timeout=20)
                if resp.status_code == 200:
                    matches = re.findall(
                        r'href=["\']([^"\']*\.pdf[^"\']*)["\']',
                        resp.text, re.IGNORECASE,
                    )
                    for match in matches[:5]:
                        candidate = (
                            match if match.startswith("http")
                            else f"{BORA_BASE}{match}"
                        )
                        try:
                            r2 = await client.get(candidate, timeout=15)
                            ct = r2.headers.get("content-type", "")
                            if r2.status_code == 200 and ("pdf" in ct or match.endswith(".pdf")):
                                pdf_url = candidate
                                break
                        except Exception:
                            continue
            except Exception as e:
                logger.debug("Fallback HTML BORA falló para %s: %s", url_bora, e)

        if not pdf_url:
            logger.warning("No se encontró PDF para %s", norma_data.get("norma_id"))
            return None

        # Descargar el PDF
        try:
            resp = await client.get(pdf_url, timeout=60)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("pdf" in ct or len(resp.content) > 1000):
                destino_path.parent.mkdir(parents=True, exist_ok=True)
                destino_path.write_bytes(resp.content)
                logger.info("PDF BORA OK: %s → %s", pdf_url, destino)
                return destino
        except Exception as e:
            logger.warning("Error descargando PDF %s: %s", pdf_url, e)

    return None


async def _sleep(segundos: float) -> None:
    import asyncio
    await asyncio.sleep(segundos)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="Scraper BORA — DAs presupuestarias JGM")
    p.add_argument("--desde", default="10/12/2023", help="Fecha inicio DD/MM/YYYY")
    p.add_argument("--hasta", default=None, help="Fecha fin DD/MM/YYYY")
    p.add_argument("--output", default=None, help="Guardar JSON en este archivo")
    args = p.parse_args()

    normas = asyncio.run(buscar_normas_bora(desde=args.desde, hasta=args.hasta))

    print(f"\n{'─'*90}")
    print(f"{'Fecha':12}  {'DA':15}  {'Tipo':14}  {'Título'}")
    print(f"{'─'*90}")
    for n in normas:
        print(
            f"{n['fecha_boletin']:12}  "
            f"DA-{n['numero']:>5}/{n['anio']}  "
            f"{n['tipo_accion']:14}  "
            f"{n['titulo'][:45]}"
        )
    print(f"{'─'*90}")
    print(f"Total: {len(normas)} DAs presupuestarias desde BORA\n")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(normas, f, ensure_ascii=False, indent=2)
        print(f"Guardado: {args.output}")