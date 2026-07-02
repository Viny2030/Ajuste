import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        url = "https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231"
        r = await client.get(url)
        # Buscar cualquier referencia a anexos o ids de avisos
        for linea in r.text.splitlines():
            if any(k in linea.lower() for k in ['anexo', 'adjunto', '7277', 'aviso', 'primera/']):
                print(linea.strip()[:150])

asyncio.run(test())
