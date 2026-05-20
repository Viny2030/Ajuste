import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        # Buscar el JS que define descargarPDFAnexo
        for js_url in [
            "https://www.boletinoficial.gob.ar/js/bora.js",
            "https://www.boletinoficial.gob.ar/js/app.js",
            "https://www.boletinoficial.gob.ar/js/main.js",
        ]:
            r = await client.get(js_url)
            if r.status_code == 200 and "descargarPDF" in r.text:
                idx = r.text.find("descargarPDFAnexo")
                print(f"Encontrado en {js_url}:")
                print(r.text[idx:idx+300])
                break
        else:
            # Buscar en la página principal los scripts
            r = await client.get("https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231")
            scripts = re.findall(r'<script src="([^"]+)"', r.text)
            print("Scripts:", scripts[:10])

asyncio.run(test())
