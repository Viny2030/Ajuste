import asyncio, httpx, re
from pathlib import Path

async def test():
    url_norma = "http://servicios.infoleg.gob.ar/infolegInternet/anexos/395000-399999/399031/norma.htm"
    destino = "data/raw_pdfs/DA-284-2024.pdf"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
        resp = await client.get(url_norma)
        base_url = url_norma.rsplit('/', 1)[0] + '/'
        matches = re.findall(r'href=[\"\'"]([^"\']+\.pdf[^"\']*)["\']', resp.text, re.I)
        print("PDFs encontrados:", matches)
        for match in matches[:3]:
            if match.startswith("http"):
                pdf_url = match
            elif match.startswith("/"):
                pdf_url = "https://servicios.infoleg.gob.ar" + match
            else:
                pdf_url = base_url + match
            print(f"Descargando: {pdf_url}")
            r2 = await client.get(pdf_url)
            print(f"Status: {r2.status_code}, size: {len(r2.content)} bytes")
            if r2.status_code == 200 and len(r2.content) > 1000:
                Path(destino).write_bytes(r2.content)
                print(f"Guardado en: {destino}")
                break

asyncio.run(test())
