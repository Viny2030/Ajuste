import sys
sys.path.insert(0, '.')
import httpx, asyncio

async def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231",
        "Accept": "application/pdf,*/*",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        urls = [
            "https://www.boletinoficial.gob.ar/pdf/download_anexo?seccion=primera&nroAnexo=1&idAnexo=7277085&fecha=20241231",
            "https://www.boletinoficial.gob.ar/pdf/download_anexo/7277085/1/20241231",
            "https://www.boletinoficial.gob.ar/pdf/download_anexo?idAnexo=7277085&numero=1&fecha=20241231&seccion=primera",
        ]
        for url in urls:
            r = await client.get(url)
            ct = r.headers.get("content-type","")
            print(f"[{r.status_code}] {len(r.content):,}b | {ct[:40]} | {url[-60:]}")

asyncio.run(test())
