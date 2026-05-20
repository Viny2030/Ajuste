import sys
sys.path.insert(0, '.')
import httpx, re, asyncio

async def test():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        # Buscar en todos los JS de la página
        r = await client.get("https://www.boletinoficial.gob.ar/detalleAviso/primera/319060/20241231")
        scripts = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', r.text)
        print("Scripts encontrados:", scripts)
        for s in scripts:
            url = s if s.startswith("http") else f"https://www.boletinoficial.gob.ar{s}"
            r2 = await client.get(url)
            if r2.status_code == 200 and "descargarPDF" in r2.text:
                idx = r2.text.find("descargarPDFAnexo")
                print(f"\nEncontrado en {url}:")
                print(r2.text[idx:idx+400])

asyncio.run(test())
