import sys
sys.path.insert(0, '.')
import httpx, asyncio
from pathlib import Path

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        for num_anexo in [1, 2]:
            url = f"https://www.boletinoficial.gob.ar/pdf/download_anexo/primera/{num_anexo}/7277085/20241231"
            r = await client.get(url)
            ct = r.headers.get("content-type","")
            print(f"Anexo {num_anexo}: [{r.status_code}] {len(r.content):,} bytes | {ct}")
            if r.status_code == 200 and "pdf" in ct:
                Path(f"data/raw_pdfs/DA-1022-2024-anexo{num_anexo}.pdf").write_bytes(r.content)
                print(f"  -> Guardado")

asyncio.run(test())
