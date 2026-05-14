# app/scrapers/bora_scraper.py
"""
Scraper del Boletín Oficial de la República Argentina (BORA).

ESTRATEGIA REAL (ingeniería inversa del frontend):
  1. GET /seccion/primera/{YYYYMMDD}
     → HTML con JSON embebido de fechas habilitadas del año (diasHabilitados)
     → Cookies de sesión necesarias para las llamadas AJAX

  2. GET /seccion/actualizar/primera?pag=N&fecha=YYYYMMDD  (con cookies)
     → JSON: {"html": "...", "hay_mas_datos": bool, "sig_pag": N}
     → HTML con todos los avisos del día, paginado

  Estructura de cada aviso en el HTML:
    <a href="/detalleAviso/primera/{id}/{fecha}?anexos=1">  ← tiene PDF adjunto
    <a href="/detalleAviso/primera/{id}/{fecha}">
      <div class="linea-aviso">
        <p class="item">PRESUPUESTO</p>
        <p class="item-detalle"><small>Decisión Administrativa 470/2024</small></p>
        <p class="item-detalle"><small>DA-2024-470-APN-JGM - Modificación.</small></p>
      </div>

  Filtramos: avisos con ?anexos=1 + "PRESUPUESTO" o "DECISIONES ADMINISTRATIVAS"
             + keywords presupuestarios en el sumario.

Esta fuente cubre el GAP de días recientes no indexados aún en el CSV de Infoleg.

Uso:
  python -m app.scrapers.bora_scraper --desde 01/01/2024 --hasta 31/12/2024
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BORA_BASE    = "https://www.boletinoficial.gob.ar"
BORA_SECCION = f"{BORA_BASE}/seccion/primera"
BORA_AVISO   = f"{BORA_BASE}/seccion/actualizar/primera"

HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "es-AR,es;q=0.9",
}

HEADERS_AJAX = {
    **HEADERS_HTML,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

_KEYWORDS_OK = [
    "presupuest", "crédito", "credito",
    "modificac", "distributivo", "reduc", "amplia", "reasign",
]
_DESCARTE = [
    "designacion", "designación", "planta de personal",
    "contratacion directa", "contratación directa",
    "estructura organizativa",
]

VERBOS_REDUCCION  = ["suprim", "reduccion", "reducción", "disminuy", "eliminase"]
VERBOS_AMPLIACION = ["amplias", "ampliación", "ampliacion", "incremento", "autorizase"]
VERBOS_REASIGN    = ["transfier", "reasign", "redistrib"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _detectar_tipo_accion(texto: str) -> str:
    t = texto.lower()
    if any(v in t for v in VERBOS_REDUCCION):  return "REDUCCION"
    if any(v in t for v in VERBOS_REASIGN):    return "REASIGNACION"
    if any(v in t for v in VERBOS_AMPLIACION): return "AMPLIACION"
    return "MODIFICACION"


def _es_presupuestaria(titulo: str, sumario: str = "") -> bool:
    texto = (titulo + " " + sumario).lower()
    if not any(k in texto for k in _KEYWORDS_OK):
        return False
    if any(k in texto for k in _DESCARTE):
        return False
    return True


def _limpiar(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


# ── Obtención de fechas habilitadas via API ───────────────────────────────────

async def _obtener_fechas_anio(
    client: httpx.AsyncClient,
    anio: int,
    cookies: dict,
) -> list[str]:
    """
    Consulta el endpoint real del BORA para obtener las fechas habilitadas de un año.
    GET /calendario/dias_publicacion/{anio}/primera
    Retorna lista de strings YYYYMMDD.
    """
    url = f"{BORA_BASE}/calendario/dias_publicacion/{anio}/primera"
    headers = {
        **HEADERS_AJAX,
        "Referer": f"{BORA_BASE}/seccion/primera/{anio}0102",
    }
    try:
        r = await client.get(url, cookies=cookies, headers=headers, timeout=15)
        if r.status_code != 200:
            logger.warning("Fechas BORA %s → HTTP %s", anio, r.status_code)
            return []
        raw = r.text.replace("&quot;", '"')
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        fechas = parsed.get("fechas", []) if isinstance(parsed, dict) else []
        return [f for f in fechas if isinstance(f, str) and len(f) == 8]
    except Exception as e:
        logger.warning("Error obteniendo fechas BORA %s: %s", anio, e)
        return []


# ── Resolución de número real de DA ──────────────────────────────────────────

async def _resolver_numero_da(
    client: httpx.AsyncClient,
    id_aviso: str,
    fecha_str: str,
    anio_fallback: str,
) -> tuple[str, str]:
    """
    Consulta /detalleAviso/primera/{id}/{fecha} para extraer el número real de la DA.
    Se usa cuando el listado del BORA muestra solo "PRESUPUESTO" sin número.
    Retorna (numero, anio).
    """
    url = f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_str}"
    try:
        r = await client.get(url, headers=HEADERS_HTML, timeout=15)
        if r.status_code != 200:
            logger.debug("_resolver_numero_da HTTP %s para id_aviso=%s", r.status_code, id_aviso)
            return id_aviso, anio_fallback

        texto = r.text

        # Patrón 1: "Decisión Administrativa 470/2024" en cualquier parte del HTML
        m = re.search(
            r'Decisi[oó]n\s+Administrativa\s+N[°º]?\s*(\d+)[/\-](\d{4})',
            texto, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'Decisi[oó]n\s+Administrativa\s+(\d+)/(\d{4})',
                texto, re.IGNORECASE,
            )
        if m:
            logger.info(
                "DA resuelta (patrón 1): id_aviso=%s → DA-%s/%s",
                id_aviso, m.group(1), m.group(2),
            )
            return m.group(1), m.group(2)

        # Patrón 2: "DA-2024-470-APN-JGM" (GDE)
        m = re.search(r'\bDA-(\d{4})-(\d+)-', texto, re.IGNORECASE)
        if m:
            logger.info(
                "DA resuelta (patrón 2 GDE): id_aviso=%s → DA-%s/%s",
                id_aviso, m.group(2), m.group(1),
            )
            return m.group(2), m.group(1)

        # Patrón 3: título de página <title>..N/YYYY...
        m = re.search(r'<title>[^<]*?(\d+)/(\d{4})', texto)
        if m:
            logger.info(
                "DA resuelta (patrón 3 title): id_aviso=%s → DA-%s/%s",
                id_aviso, m.group(1), m.group(2),
            )
            return m.group(1), m.group(2)

        # Patrón 4: cualquier "NNN/YYYY" donde YYYY sea el año esperado
        # Solo si NO es una Ley de presupuesto (Ley NNNNN)
        if not re.search(r'\bley\s+\d+', texto[:500], re.IGNORECASE):
            m = re.search(rf'(\d{{1,4}})/({anio_fallback})', texto)
            if m:
                logger.info(
                    "DA resuelta (patrón 4 genérico): id_aviso=%s → DA-%s/%s",
                    id_aviso, m.group(1), anio_fallback,
                )
                return m.group(1), anio_fallback

        logger.warning(
            "No se pudo resolver número para id_aviso=%s fecha=%s — se usará id_aviso como fallback",
            id_aviso, fecha_str,
        )

    except Exception as e:
        logger.debug("_resolver_numero_da error id_aviso=%s: %s", id_aviso, e)

    return id_aviso, anio_fallback


# ── Parser HTML de avisos (async para poder resolver números) ─────────────────

async def _parsear_html_avisos(
    client: httpx.AsyncClient,
    html: str,
    fecha_iso: str,
) -> list[dict]:
    """
    Parsea el HTML devuelto por /seccion/actualizar/primera y extrae
    las DAs presupuestarias.

    Cuando el número no puede extraerse del listado, consulta el detalle
    del aviso para obtener el número real (evita usar id_aviso como número).

    Estructura observada:
      <a href="/detalleAviso/primera/{id}/{fecha}?anexos=1">  ← paperclip
      <a href="/detalleAviso/primera/{id}/{fecha}">           ← link principal
        <div class="linea-aviso">
          <p class="item">PRESUPUESTO</p>
          <p class="item-detalle"><small>Decisión Administrativa 470/2024</small></p>
          <p class="item-detalle"><small>DA-2024-470-APN-JGM - Modificación.</small></p>
        </div>
    """
    fecha_bora_str = fecha_iso.replace("-", "")
    anio = fecha_iso[:4]
    resultados = []

    # Buscar todos los ids que tienen ?anexos=1 (tienen PDF adjunto)
    ids_con_anexo = set(re.findall(
        rf'detalleAviso/primera/(\d+)/{fecha_bora_str}\?anexos=1', html
    ))

    for id_aviso in ids_con_anexo:
        # Buscar el link principal (sin ?anexos=1)
        patron_link = rf'href="/detalleAviso/primera/{id_aviso}/{fecha_bora_str}"'
        pos = html.find(patron_link)
        if pos == -1:
            pos = html.find(f'href="/detalleAviso/primera/{id_aviso}/')
        if pos == -1:
            continue

        # Extraer el bloque linea-aviso después del link
        pos_div = html.find('<div class="linea-aviso">', pos)
        if pos_div == -1 or pos_div > pos + 500:
            continue
        pos_fin = html.find('</div>', pos_div)
        bloque = html[pos_div:pos_fin + 10]

        # Extraer campos
        items  = re.findall(r'<p class="item">([^<]+)</p>', bloque)
        smalls = re.findall(r'<small>([^<]+)</small>', bloque)

        titulo    = _limpiar(items[0])  if items          else ""
        norma_str = _limpiar(smalls[0]) if len(smalls) > 0 else ""
        sumario   = _limpiar(smalls[1]) if len(smalls) > 1 else ""

        # Filtrar: solo DAs presupuestarias
        texto_completo = (titulo + " " + norma_str + " " + sumario).lower()
        if not any(k in texto_completo for k in _KEYWORDS_OK):
            continue
        if any(k in texto_completo for k in _DESCARTE):
            continue
        if "decisión administrativa" not in texto_completo and \
           "decision administrativa" not in texto_completo and \
           "da-" not in texto_completo.replace(" ", ""):
            if "presupuest" not in titulo.lower():
                continue

        # Excluir leyes de presupuesto (Ley NNNNN) — no son DAs de modificación
        if re.search(r'\bley\s+\d+', texto_completo, re.IGNORECASE):
            if "decision administrativa" not in texto_completo and \
               "decisión administrativa" not in texto_completo and \
               "da-" not in texto_completo.replace(" ", ""):
                continue

        # ── Extraer número de la DA ──────────────────────────────────────────
        numero  = None
        anio_da = anio

        # Intento 1: "Decisión Administrativa 470/2024" en norma_str
        m_num = re.search(
            r'(?:Decisi[oó]n\s+Administrativa|D\.A\.)\s+N[°º]?\s*(\d+)[/\-](\d{4})',
            norma_str, re.IGNORECASE,
        )
        if not m_num:
            m_num = re.search(
                r'(?:Decisi[oó]n\s+Administrativa|D\.A\.)\s+(\d+)/(\d{4})',
                norma_str, re.IGNORECASE,
            )
        if m_num:
            numero  = m_num.group(1)
            anio_da = m_num.group(2)

        # Intento 2: "DA-YYYY-NNN-" en sumario (GDE)
        if numero is None:
            m_gde = re.search(r'\bDA-(\d{4})-(\d+)-', sumario, re.IGNORECASE)
            if m_gde:
                numero  = m_gde.group(2)
                anio_da = m_gde.group(1)

        # Intento 3: consultar el detalle del aviso (HTTP extra)
        if numero is None:
            logger.debug(
                "Número no encontrado en listado para id_aviso=%s — consultando detalle",
                id_aviso,
            )
            numero, anio_da = await _resolver_numero_da(
                client, id_aviso, fecha_bora_str, anio
            )

        norma_id = f"DA-{numero}-{anio_da}"
        url_bora = f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_bora_str}"

        resultados.append({
            "norma_id":       norma_id,
            "tipo_norma":     "DA",
            "numero":         numero,
            "anio":           int(anio_da),
            "fecha_boletin":  fecha_iso,
            "numero_boletin": "",
            "pagina_boletin": "",
            "organismo":      "Jefatura de Gabinete de Ministros",
            "titulo":         titulo or norma_str,
            "sumario":        sumario[:500],
            "texto_resumido": (norma_str + " " + sumario)[:1000],
            "tipo_accion":    _detectar_tipo_accion(texto_completo),
            "url_infoleg":    url_bora,
            "url_bora":       url_bora,
            "id_aviso_bora":  id_aviso,
            "fecha_bora_str": fecha_bora_str,
            "pdf_hash":       _sha256(norma_id),
            "fuente":         "BORA_HTML",
        })

    return resultados


# ── Scraper por fecha ─────────────────────────────────────────────────────────

async def _scraper_fecha(
    client: httpx.AsyncClient,
    fecha: date,
    cookies: dict,  # no usado — cookies frescas se obtienen por fecha
) -> list[dict]:
    """
    Descarga y parsea todos los avisos de una fecha.
    """
    fecha_iso = fecha.strftime("%Y-%m-%d")
    fecha_str = fecha.strftime("%Y%m%d")

    # Cookies frescas para esta fecha especifica
    try:
        r_sesion = await client.get(
            f"{BORA_SECCION}/{fecha_str}",
            headers=HEADERS_HTML,
            timeout=15,
        )
        if str(r_sesion.url).rstrip("/") == BORA_BASE.rstrip("/"):
            return []
        cookies_fecha = dict(r_sesion.cookies)
    except Exception as e:
        logger.warning("No se pudo obtener sesion para %s: %s", fecha_iso, e)
        return []

    headers = {
        **HEADERS_AJAX,
        "Referer": f"{BORA_SECCION}/{fecha_str}",
    }

    html_total = ""
    pag = 1

    while True:
        try:
            r = await client.get(
                BORA_AVISO,
                params={"pag": str(pag), "fecha": fecha_str},
                headers=headers,
                cookies=cookies_fecha,
                timeout=20,
            )
            if r.status_code != 200:
                logger.debug("BORA %s pag=%s → HTTP %s", fecha_iso, pag, r.status_code)
                break

            data = r.json()
            html_pag = data.get("html", "")
            html_total += html_pag

            if not data.get("hay_mas_datos") or not html_pag:
                break
            pag = data.get("sig_pag", pag + 1)

        except Exception as e:
            logger.warning("Error BORA %s pag=%s: %s", fecha_iso, pag, e)
            break

        await asyncio.sleep(0.2)

    if not html_total:
        return []

    # _parsear_html_avisos es ahora async y recibe el client para resolver números
    avisos = await _parsear_html_avisos(client, html_total, fecha_iso)
    if avisos:
        logger.info("BORA %s: %d DAs presupuestarias", fecha_iso, len(avisos))
    return avisos


# ── Sesión ────────────────────────────────────────────────────────────────────

async def _obtener_sesion(
    client: httpx.AsyncClient,
    anio: int,
) -> tuple[dict, list[str]]:
    """
    Obtiene cookies de sesión y fechas habilitadas del año via el endpoint real:
    GET /calendario/dias_publicacion/{anio}/primera
    Retorna (cookies, fechas_habilitadas_YYYYMMDD).
    """
    from datetime import date as _date, timedelta as _td
    cookies: dict = {}

    # Necesitamos cookies válidas — las obtenemos visitando cualquier fecha del año
    fecha_ref = f"{anio}0102"
    try:
        r = await client.get(
            f"{BORA_SECCION}/{fecha_ref}",
            headers=HEADERS_HTML,
            timeout=15,
        )
        cookies = dict(r.cookies)
    except Exception as e:
        logger.warning("Error obteniendo cookies BORA %s: %s", anio, e)

    # Consultar endpoint real de fechas habilitadas
    fechas = await _obtener_fechas_anio(client, anio, cookies)

    if fechas:
        logger.info("Sesión BORA año %s: %d fechas habilitadas", anio, len(fechas))
    else:
        logger.warning("No se pudieron obtener fechas habilitadas para %s", anio)

    return cookies, fechas


# ── Función principal ─────────────────────────────────────────────────────────

async def buscar_normas_bora(
    desde: str = "10/12/2023",
    hasta: Optional[str] = None,
) -> list[dict]:
    """
    Busca DAs presupuestarias del JGM en el BORA usando el endpoint AJAX real
    del frontend: GET /seccion/actualizar/primera?pag=N&fecha=YYYYMMDD
    """
    try:
        desde_dt = datetime.strptime(desde, "%d/%m/%Y").date()
    except ValueError:
        desde_dt = date(2023, 12, 10)

    hasta_dt = (
        datetime.strptime(hasta, "%d/%m/%Y").date()
        if hasta else date.today()
    )

    resultados: dict[str, dict] = {}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        anios = list(range(desde_dt.year, hasta_dt.year + 1))
        fechas_habilitadas: set[str] = set()

        for anio in anios:
            _, fechas = await _obtener_sesion(client, anio)
            fechas_habilitadas.update(fechas)

        desde_str = desde_dt.strftime("%Y%m%d")
        hasta_str = hasta_dt.strftime("%Y%m%d")
        fechas_a_scraper = sorted([
            f for f in fechas_habilitadas
            if desde_str <= f <= hasta_str
        ])

        logger.info(
            "BORA scraper: %d fechas a procesar entre %s y %s",
            len(fechas_a_scraper), desde_dt, hasta_dt,
        )

        if not fechas_a_scraper:
            return []

        primera_fecha = fechas_a_scraper[0]
        r_sesion = await client.get(
            f"{BORA_SECCION}/{primera_fecha}",
            headers=HEADERS_HTML,
            timeout=15,
        )
        cookies = dict(r_sesion.cookies)
        logger.info("Cookies de sesión obtenidas desde %s", primera_fecha)

        for fecha_str in fechas_a_scraper:
            fecha = datetime.strptime(fecha_str, "%Y%m%d").date()
            avisos = await _scraper_fecha(client, fecha, cookies)
            for aviso in avisos:
                nid = aviso["norma_id"]
                if nid not in resultados:
                    resultados[nid] = aviso
            await asyncio.sleep(0.3)

    normas = sorted(resultados.values(), key=lambda x: x["fecha_boletin"])
    logger.info("BORA scraper: %d DAs presupuestarias en total", len(normas))
    return normas


# ── Descarga de PDFs ──────────────────────────────────────────────────────────

async def _obtener_pdf_url(
    client: httpx.AsyncClient,
    id_aviso: str,
    fecha_boletin: str,
) -> Optional[str]:
    """Busca la URL del PDF del Anexo en la página de detalle del aviso."""
    url_detalle = f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_boletin}?anexos=1"
    try:
        r = await client.get(url_detalle, headers=HEADERS_HTML, timeout=15)
        if r.status_code == 200:
            pdfs = re.findall(
                r'href="([^"]+\.pdf[^"]*)"', r.text, re.IGNORECASE
            )
            if pdfs:
                url = pdfs[0]
                return url if url.startswith("http") else BORA_BASE + url

            anexos = re.findall(
                r'descargarPDFAnexo\(\s*"primera"\s*,\s*"(\d+)"\s*,\s*"(\d+)"',
                r.text,
            )
            if anexos:
                import base64
                nro_anexo, id_anexo = anexos[0]
                resp_pdf = await client.post(
                    f"{BORA_BASE}/pdf/download_anexo",
                    data={
                        "seccion": "primera",
                        "nroAnexo": nro_anexo,
                        "idAnexo": id_anexo,
                        "fechaPublicacion": fecha_boletin,
                    },
                    headers={**HEADERS_AJAX, "Referer": url_detalle},
                    timeout=60,
                )
                if resp_pdf.status_code == 200:
                    data = resp_pdf.json()
                    if data.get("pdfBase64"):
                        return f"base64:{data['pdfBase64']}"
    except Exception as e:
        logger.debug("_obtener_pdf_url error %s: %s", id_aviso, e)

    return None


async def descargar_pdf_bora(
    norma_data: dict,
    destino: str,
) -> Optional[str]:
    """
    Descarga el PDF del Anexo de una DA desde el BORA.
    """
    destino_path = Path(destino)
    if destino_path.exists() and destino_path.stat().st_size > 500:
        return destino

    id_aviso  = norma_data.get("id_aviso_bora", "")
    fecha_str = norma_data.get("fecha_bora_str", "")

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resultado = await _obtener_pdf_url(client, id_aviso, fecha_str)

        if not resultado:
            logger.warning("No se encontró PDF para %s", norma_data.get("norma_id"))
            return None

        if resultado.startswith("base64:"):
            import base64
            pdf_bytes = base64.b64decode(resultado[7:])
            if len(pdf_bytes) > 1000:
                destino_path.parent.mkdir(parents=True, exist_ok=True)
                destino_path.write_bytes(pdf_bytes)
                logger.info("PDF BORA (base64) OK: %s", destino)
                return destino

        try:
            r = await client.get(resultado, headers=HEADERS_HTML, timeout=60)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ("pdf" in ct or len(r.content) > 1000):
                destino_path.parent.mkdir(parents=True, exist_ok=True)
                destino_path.write_bytes(r.content)
                logger.info("PDF BORA OK: %s → %s", resultado, destino)
                return destino
        except Exception as e:
            logger.warning("Error descargando PDF %s: %s", resultado, e)

    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="Scraper BORA — DAs presupuestarias JGM")
    p.add_argument("--desde", default="10/12/2023", help="Fecha inicio DD/MM/YYYY")
    p.add_argument("--hasta", default=None,          help="Fecha fin DD/MM/YYYY")
    p.add_argument("--output", default=None,          help="Guardar JSON en este archivo")
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
    print(f"Total: {len(normas)} DAs presupuestarias\n")

    if args.output:
        import json
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(normas, f, ensure_ascii=False, indent=2)
        print(f"Guardado: {args.output}")