# app/scrapers/bora_discovery.py
import asyncio
from playwright.async_api import async_playwright
from datetime import datetime

class BoraScraper:
    def __init__(self):
        self.base_url = "https://www.boletinoficial.gob.ar/seccion/primera"

    async def buscar_decretos(self, palabra_clave="Presupuesto", desde="2023-01-01"):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Navegar a la sección de legislación
            await page.goto(self.base_url)
            
            # Aquí interactuamos con los filtros del buscador
            # Nota: Los selectores pueden variar, se recomienda inspeccionarlos
            await page.fill("#p_filtro", palabra_clave)
            await page.fill("#p_fecha_desde", desde)
            await page.click("#btnBusquedaAvanzada")
            
            await page.wait_for_selector(".detalle-norma")
            
            resultados = []
            normas = await page.query_selector_all(".detalle-norma")
            
            for norma in normas:
                titulo = await norma.inner_text()
                link = await norma.get_attribute("href")
                resultados.append({
                    "titulo": titulo,
                    "url": f"https://www.boletinoficial.gob.ar{link}",
                    "fecha_captura": datetime.now().isoformat()
                })
            
            await browser.close()
            return resultados

# Para probarlo:
# asyncio.run(BoraScraper().buscar_decretos())
