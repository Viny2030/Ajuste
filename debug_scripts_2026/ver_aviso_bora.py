import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        url = "https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231"
        r = await client.get(url)
        print(f"Status: {r.status_code}")
        # Buscar IDs de avisos relacionados / anexos
        ids = re.findall(r'(\d{6,})', r.text)
        ids_unicos = sorted(set(ids), key=lambda x: int(x))
        print("IDs en la página:", ids_unicos[:20])
        # Buscar links a PDF
        pdfs = re.findall(r'pdf[^"\'<>\s]*', r.text, re.I)
        print("Referencias PDF:", pdfs[:10])

asyncio.run(test())
