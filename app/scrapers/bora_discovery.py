# app/scrapers/bora_discovery.py
"""
Scraper del Boletín Oficial (BORA) orientado a Decisiones Administrativas
del Jefe de Gabinete de Ministros (JGM) que modifican el presupuesto.
Detecta automáticamente el tipo de acción (REDUCCIÓN / REASIGNACIÓN / AMPLIACIÓN).
"""
import asyncio
import hashlib
import re
import urllib.parse
from datetime import datetime

import httpx
from playwright.async_api import async_playwright


# ─── Palabras clave por tipo de norma ────────────────────────────

TERMINOS_BUSQUEDA = [
    "Presupuesto General",
    "Modificación Presupuestaria",
    "Decisión Administrativa JGM",
    "crédito presupuestario",
]

VERBOS_REDUCCION = ["suprímase", "redúzcase", "disminúyase", "déjase sin efecto",
                    "elimínase", "suprimir", "reducción"]
VERBOS_AMPLIACION = ["ampliase", "increméntese", "autorízase", "refuerzo de crédito",
                     "ampliación"]
VERBOS_REASIGNACION = ["transfiérase", "reasígnese", "redistribúyase"]

PATRON_DA = re.compile(
    r"(DA|Decisión Administrativa|DNU|Decreto)[^\d]*(\d{1,5})[^\d](\d{4})",
    re.IGNORECASE
)


def _detectar_tipo_accion(texto: str) -> str:
    texto_lower = texto.lower()
    if any(v in texto_lower for v in VERBOS_REDUCCION):
        return "REDUCCION"
    if any(v in texto_lower for v in VERBOS_REASIGNACION):
        return "REASIGNACION"
    if any(v in texto_lower for v in VERBOS_AMPLIACION):
        return "AMPLIACION"
    return "OTRO"


def _sha256(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# ─── Scraper principal ───────────────────────────────────────────

class BoraScraper:
    BASE = "https://www.boletinoficial.gob.ar/seccion/primera"

    def __init__(self):
        self.resultados_raw = []

    async def buscar_normas(
        self,
        palabras: list[str] | None = None,
        desde: str = "01/01/2023",
    ) -> list[dict]:
        palabras = palabras or TERMINOS_BUSQUEDA
        resultados = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            hoy = datetime.now().strftime("%d/%m/%Y")

            for termino in palabras:
                params = {
                    "p_filtro": termino,
                    "p_fecha_desde": desde,
                    "p_fecha_hasta": hoy,
                }
                url = f"{self.BASE}?{urllib.parse.urlencode(params)}"
                print(f"🔍 Buscando: '{termino}' ({desde} ➔ {hoy})")

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await asyncio.sleep(8)

                    items = await page.query_selector_all(".item-norma")
                    if not items:
                        print(f"  ℹ️  Sin resultados para '{termino}'")
                        continue

                    for item in items:
                        titulo_el = await item.query_selector("h4")
                        titulo = (await titulo_el.inner_text()).strip() if titulo_el else ""

                        link_el = await item.query_selector("a")
                        href = await link_el.get_attribute("href") if link_el else ""

                        # Fecha publicación
                        fecha_el = await item.query_selector(".fecha")
                        fecha_txt = (await fecha_el.inner_text()).strip() if fecha_el else ""

                        if not href:
                            continue

                        url_norma = f"https://www.boletinoficial.gob.ar{href}"

                        # Parsear número/año de norma
                        m = PATRON_DA.search(titulo)
                        tipo = m.group(1).upper() if m else "DA"
                        numero = m.group(2) if m else "0"
                        anio = int(m.group(3)) if m else datetime.now().year

                        # ID canónico p/ dedup
                        norma_id = f"{tipo}-{numero}-{anio}"

                        resultados.append({
                            "norma_id": norma_id,
                            "tipo_norma": tipo,
                            "numero": numero,
                            "anio": anio,
                            "titulo": titulo,
                            "url_bora": url_norma,
                            "fecha_publicacion": fecha_txt,
                            "tipo_accion": _detectar_tipo_accion(titulo),
                            "pdf_hash": _sha256(url_norma),
                        })

                except Exception as e:
                    print(f"  ❌ Error en '{termino}': {e}")
                    continue

            await browser.close()

        # Deduplicar por norma_id
        vistos = set()
        unicos = []
        for r in resultados:
            if r["norma_id"] not in vistos:
                vistos.add(r["norma_id"])
                unicos.append(r)

        print(f"✅ Total normas únicas detectadas: {len(unicos)}")
        return unicos


# ─── Descargador de PDFs de anexos ──────────────────────────────

async def descargar_pdf_norma(url_norma: str, destino: str) -> str | None:
    """
    Navega a la página de la norma en BORA, encuentra el link al PDF del anexo
    y lo descarga. Retorna el path local o None si falla.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await (await browser.new_context()).new_page()
        try:
            await page.goto(url_norma, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(4)

            # El BORA suele tener links ".pdf" o botones "Ver Anexo"
            pdf_link = await page.query_selector("a[href$='.pdf'], a:has-text('Anexo')")
            if not pdf_link:
                return None

            pdf_url = await pdf_link.get_attribute("href")
            if not pdf_url.startswith("http"):
                pdf_url = f"https://www.boletinoficial.gob.ar{pdf_url}"

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(pdf_url)
                resp.raise_for_status()
                with open(destino, "wb") as f:
                    f.write(resp.content)
            return destino
        except Exception as e:
            print(f"❌ PDF no descargado ({url_norma}): {e}")
            return None
        finally:
            await browser.close()