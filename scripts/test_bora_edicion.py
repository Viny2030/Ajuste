import sys
sys.path.insert(0, '.')
import httpx, asyncio, re

async def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.boletinoficial.gob.ar/",
        "Origin": "https://www.boletinoficial.gob.ar",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=15) as client:
        # Probar endpoint de edicion
        for url in [
            "https://www.boletinoficial.gob.ar/normas/buscarAviso?numeroBoletin=35437&seccion=1&fecha=20240606",
            "https://www.boletinoficial.gob.ar/normas/edicion/35437/20240606/1",
            "https://www.boletinoficial.gob.ar/seccion/primera/35437/20240606",
        ]:
            r = await client.get(url)
            print(f"[{r.status_code}] {url}")
            if r.status_code == 200:
                print(f"  Content-Type: {r.headers.get('content-type','')}")
                ids = re.findall(r'"id"\s*:\s*(\d{5,})', r.text)
                print(f"  IDs encontrados: {ids[:5]}")

asyncio.run(test())
