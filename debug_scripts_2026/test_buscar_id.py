import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        # El primer ID de 20240606 es 308731, el correcto es 308747
        # Diferencia = 16. Probemos de 308731 a 308760
        fecha = "20240606"
        for id_av in range(308731, 308760):
            url = f"https://www.boletinoficial.gob.ar/detalleAviso/primera/{id_av}/{fecha}"
            r = await client.get(url)
            # Buscar si tiene anexo presupuestario
            if "descargarPDFAnexo" in r.text and "presupuest" in r.text.lower():
                print(f"ENCONTRADO id={id_av}")
                anexo_ids = re.findall(r'descargarPDFAnexo\([^)]+\)', r.text)
                print(f"  Anexos: {anexo_ids[:3]}")
                break
            elif r.status_code == 200 and "Decisión Administrativa" in r.text:
                print(f"id={id_av}: DA encontrada")

asyncio.run(test())
