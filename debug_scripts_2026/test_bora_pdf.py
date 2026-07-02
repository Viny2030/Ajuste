import sys
sys.path.insert(0, '.')
import httpx, asyncio

# id_aviso reales obtenidos del BORA
normas_bora = [
    ("DA-470-2024",  "308747", "20240606"),
    ("DA-858-2024",  "313144", "20240902"),  
    ("DA-861-2024",  "313144", "20240902"),
    ("DA-910-2024",  "315023", "20240930"),
    ("DA-1015-2024", "316312", "20241031"),
    ("DA-1018-2024", "317506", "20241129"),
    ("DA-1022-2024", "319060", "20241231"),
]

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
        for norma_id, id_aviso, fecha in normas_bora:
            url = f"https://www.boletinoficial.gob.ar/pdf/aviso/primera/{id_aviso}/{fecha}"
            try:
                r = await client.get(url)
                ct = r.headers.get("content-type","")
                print(f"[{r.status_code}] {norma_id}: {len(r.content)} bytes | {ct[:30]} | {url}")
            except Exception as e:
                print(f"[ERR] {norma_id}: {e}")

asyncio.run(test())
