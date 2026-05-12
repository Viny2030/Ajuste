import asyncio
from playwright.async_api import async_playwright
import urllib.parse

async def debug_bora():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # abre ventana visible
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        params = {
            "p_filtro": "Presupuesto",
            "p_fecha_desde": "01/01/2024",
            "p_fecha_hasta": "12/05/2026"
        }
        url = f"https://www.boletinoficial.gob.ar/seccion/primera?{urllib.parse.urlencode(params)}"
        print(f"\nURL: {url}\n")

        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print("Página cargada, esperando render...")
        await asyncio.sleep(12)

        # Guardar HTML completo para inspección
        html = await page.content()
        with open("bora_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML guardado en bora_debug.html ({len(html):,} chars)")

        # Probar selectores conocidos y posibles nuevos
        selectores = [
            ".item-norma",
            ".norma",
            "article",
            ".resultado",
            ".aviso",
            "li.norma",
            ".listado-normas li",
            ".ng-scope",           # Angular apps
            "[class*='norma']",
            "[class*='aviso']",
            "[class*='item']",
            "ul li",
        ]
        print("\nSelectores encontrados:")
        for sel in selectores:
            try:
                items = await page.query_selector_all(sel)
                if items:
                    print(f"  ✅ '{sel}': {len(items)} elementos")
            except Exception:
                pass

        title = await page.title()
        print(f"\nTítulo de la página: {title}")

        text = await page.evaluate("document.body.innerText")
        print(f"\nPrimeros 800 chars del body:\n{'-'*40}\n{text[:800]}\n{'-'*40}")

        print("\nDejando el browser abierto 30 segundos para inspección manual...")
        await asyncio.sleep(30)
        await browser.close()

asyncio.run(debug_bora())