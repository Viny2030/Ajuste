import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        url = "https://www.boletinoficial.gob.ar/seccion/primera/20240606"
        r = await client.get(url)
        # Buscar contexto alrededor de cada id_aviso
        for match in re.finditer(r'detalleAviso/primera/(\d+)/20240606', r.text):
            id_av = match.group(1)
            # Tomar 200 chars alrededor para ver el contexto
            start = max(0, match.start() - 150)
            end = min(len(r.text), match.end() + 150)
            contexto = r.text[start:end].replace('\n','')
            if any(k in contexto.lower() for k in ['decisión', 'decision', 'presupuest', 'da-']):
                print(f"ID {id_av}: {contexto[:200]}")

asyncio.run(test())
