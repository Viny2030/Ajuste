import sys
sys.path.insert(0, '.')
import httpx, asyncio
from pathlib import Path

# Mapa norma_id -> (id_aviso_bora, fecha_bora)
MAPA_BORA = {
    "DA-470-2024":  ("308747", "20240606"),
    "DA-858-2024":  ("313144", "20240902"),
    "DA-861-2024":  ("313144", "20240902"),
    "DA-910-2024":  ("315023", "20240930"),
    "DA-1015-2024": ("316312", "20241031"),
    "DA-1018-2024": ("317506", "20241129"),
    "DA-1022-2024": ("319060", "20241231"),
}

async def descargar():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        for norma_id, (id_aviso, fecha) in MAPA_BORA.items():
            destino = Path(f"data/raw_pdfs/{norma_id}.pdf")
            if destino.exists():
                print(f"[SKIP] {norma_id} ya existe")
                continue
            url = f"https://www.boletinoficial.gob.ar/pdf/aviso/primera/{id_aviso}/{fecha}"
            r = await client.get(url)
            if r.status_code == 200 and len(r.content) > 1000:
                destino.write_bytes(r.content)
                print(f"[OK] {norma_id} -> {len(r.content):,} bytes")
            else:
                print(f"[FALLO] {norma_id}: status {r.status_code}")

asyncio.run(descargar())
