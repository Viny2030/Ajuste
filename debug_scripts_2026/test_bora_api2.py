import sys
sys.path.insert(0, '.')
import httpx, asyncio, re

async def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Referer": "https://www.boletinoficial.gob.ar/",
    }
    # numero_boletin de DA-470-2024 es 35437, fecha 20240606
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        url = "https://www.boletinoficial.gob.ar/normas/boletin/35437/20240606/1"
        r = await client.get(url)
        print(f"Status: {r.status_code}, Content-Type: {r.headers.get('content-type','')}")
        print(r.text[:500])

asyncio.run(test())
