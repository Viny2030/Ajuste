import asyncio
import httpx
import base64
import re
from pathlib import Path

BORA_BASE = "https://www.boletinoficial.gob.ar"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

async def main():
    id_aviso = "337567"
    fecha = "20260120"

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as c:
        url = f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha}?anexos=1"
        r = await c.get(url, headers=HEADERS)

        # Buscar TODOS los patrones de anexo incluyendo nroAnexo diferente
        pat = r'descargarPDFAnexo\(\s*"primera"\s*,\s*"(\d+)"\s*,\s*"(\d+)"'
        anexos = re.findall(pat, r.text)
        print(f"Anexos: {anexos}")

        # Mostrar también los links de descarga directa si los hay
        links = re.findall(r'href="([^"]*anexo[^"]*)"', r.text, re.IGNORECASE)
        for l in links[:10]:
            print(f"LINK: {l}")

        # Mostrar fragmento del HTML con los títulos de los anexos
        idx = r.text.find("anexo")
        if idx > 0:
            print("\nCONTEXTO ANEXOS:")
            print(r.text[max(0,idx-200):idx+500])

asyncio.run(main())