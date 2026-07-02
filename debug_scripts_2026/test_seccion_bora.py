import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        # numero_boletin=35437, fecha=20240606 para DA-470-2024
        url = "https://www.boletinoficial.gob.ar/seccion/primera/20240606"
        r = await client.get(url)
        print(f"Status: {r.status_code}")
        # Buscar id_aviso de DAs presupuestarias
        matches = re.findall(r'detalleAviso/primera/(\d+)/20240606', r.text)
        print(f"IDs de avisos: {matches[:10]}")

asyncio.run(test())
