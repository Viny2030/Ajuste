import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        url = "https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=400246"
        r = await client.get(url)
        matches = re.findall(r'detalleAviso/primera/(\d+)/(\d+)', r.text)
        print("Links BORA encontrados:", matches[:5])

asyncio.run(test())
