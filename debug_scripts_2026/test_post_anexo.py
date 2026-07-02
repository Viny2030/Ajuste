import sys
sys.path.insert(0, '.')
import httpx, asyncio
from pathlib import Path

async def test():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        for nro_anexo in [1, 2]:
            r = await client.post(
                "https://www.boletinoficial.gob.ar/pdf/download_anexo",
                data={
                    "seccion": "primera",
                    "nroAnexo": str(nro_anexo),
                    "idAnexo": "7277085",
                    "fechaPublicacion": "20241231",
                },
            )
            ct = r.headers.get("content-type","")
            print(f"Anexo {nro_anexo}: [{r.status_code}] {len(r.content):,}b | {ct}")
            if "pdf" in ct and len(r.content) > 1000:
                Path(f"data/raw_pdfs/DA-1022-2024-anexo{nro_anexo}.pdf").write_bytes(r.content)
                print(f"  -> Guardado")
            else:
                print(f"  -> {r.text[:100]}")

asyncio.run(test())
