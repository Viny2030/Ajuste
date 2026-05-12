"""
Intercepta las llamadas de red que hace el BORA para encontrar su API interna.
Corre con: python debug_bora3.py
"""
import asyncio
import json
from playwright.async_api import async_playwright
import urllib.parse

API_CALLS = []

async def debug_bora_network():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # Interceptar TODAS las requests de red
        async def on_request(request):
            url = request.url
            if any(x in url for x in ["api", "json", "buscar", "norma", "search", "fetch"]):
                API_CALLS.append({"type": "request", "url": url, "method": request.method})

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "json" in ct and "boletinoficial" in url:
                try:
                    body = await response.json()
                    API_CALLS.append({
                        "type": "json_response",
                        "url": url,
                        "status": response.status,
                        "body_preview": str(body)[:500]
                    })
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        params = {
            "p_filtro": "Presupuesto",
            "p_fecha_desde": "01/01/2024",
            "p_fecha_hasta": "12/05/2026"
        }
        url = f"https://www.boletinoficial.gob.ar/seccion/primera?{urllib.parse.urlencode(params)}"
        print(f"Navegando a: {url}")
        print("Esperando que carguen los resultados...\n")

        try:
            await page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        await asyncio.sleep(10)

        # Mostrar todas las llamadas JSON interceptadas
        print("=" * 60)
        print(f"LLAMADAS A APIs detectadas: {len(API_CALLS)}")
        print("=" * 60)
        for call in API_CALLS:
            print(f"\n[{call['type']}] {call.get('method','GET')} {call['url']}")
            if "body_preview" in call:
                print(f"  Body: {call['body_preview'][:300]}")

        # Buscar el endpoint de búsqueda directamente via JS
        print("\n" + "=" * 60)
        print("Buscando requests en el historial de performance...")
        entries = await page.evaluate("""
            () => {
                const entries = performance.getEntriesByType('resource');
                return entries
                    .filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
                    .map(e => e.name);
            }
        """)
        print(f"XHR/Fetch detectados: {len(entries)}")
        for e in entries:
            print(f"  {e}")

        # Intentar encontrar el endpoint de búsqueda manualmente
        print("\n" + "=" * 60)
        print("Intentando endpoint conocido de búsqueda avanzada...")
        api_candidates = [
            "https://www.boletinoficial.gob.ar/norma/busqueda/primera",
            "https://www.boletinoficial.gob.ar/api/norma/busqueda",
            "https://www.boletinoficial.gob.ar/seccion/primera/busqueda",
        ]
        import requests as req
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                   "X-Requested-With": "XMLHttpRequest"}
        payload = {"busqueda": "Presupuesto", "fechaDesde": "20240101", "fechaHasta": "20260512"}
        for url_c in api_candidates:
            try:
                r = req.post(url_c, json=payload, headers=headers, timeout=10)
                print(f"POST {url_c}: {r.status_code} | {r.headers.get('content-type','?')[:50]}")
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    print(f"  → {r.text[:300]}")
            except Exception as ex:
                print(f"POST {url_c}: ERROR {ex}")

        await browser.close()

asyncio.run(debug_bora_network())