import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
import urllib.parse


class BoraScraper:
    def __init__(self):
        self.base_url = "https://www.boletinoficial.gob.ar/seccion/primera"

    async def buscar_decretos(self, palabra_clave="Presupuesto", desde="10/12/2023"):
        async with async_playwright() as p:
            # Usamos headless=True para evitar consumo excesivo en tu PyCharm
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            # Forzamos el rango: Desde asunción hasta hoy
            hoy = datetime.now().strftime("%d/%m/%Y")

            # El BORA requiere parámetros específicos en la URL para búsqueda avanzada
            params = {
                "p_filtro": palabra_clave,
                "p_fecha_desde": desde,
                "p_fecha_hasta": hoy
            }
            query_string = urllib.parse.urlencode(params)
            url_directa = f"{self.base_url}?{query_string}"

            try:
                print(f"🚀 Analizando gestión actual: {desde} ➔ {hoy}")

                # Navegación con espera de carga de DOM
                await page.goto(url_directa, wait_until="domcontentloaded", timeout=60000)

                # Espera extendida para que el listado de normas se renderice
                await asyncio.sleep(10)

                resultados = []
                items = await page.query_selector_all(".item-norma")

                if not items:
                    if await page.is_visible("text=No se encontraron"):
                        print(f"ℹ️ No se detectaron decretos de '{palabra_clave}' en este periodo.")
                    else:
                        print("⚠️ Error de renderizado: La página cargó pero no mostró el listado.")
                    return []

                for item in items:
                    titulo_h4 = await item.query_selector("h4")
                    titulo = await titulo_h4.inner_text() if titulo_h4 else "Sin Título"

                    link_det = await item.query_selector("a")
                    href = await link_det.get_attribute("href") if link_det else ""

                    if href:
                        resultados.append({
                            "titulo": titulo.strip(),
                            "url": f"https://www.boletinoficial.gob.ar{href}",
                            "fecha_captura": datetime.now().isoformat()
                        })

                print(f"✅ Éxito: Se encontraron {len(resultados)} normas publicadas en la gestión.")
                return resultados

            except Exception as e:
                print(f"❌ Error en la conexión: {e}")
                return []
            finally:
                await browser.close()