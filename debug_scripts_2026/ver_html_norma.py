import httpx, re
r = httpx.get("https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=400246", follow_redirects=True, timeout=10)
# Buscar cualquier referencia a PDF o anexo
for linea in r.text.splitlines():
    if any(k in linea.lower() for k in ['pdf', 'anexo', 'adjunto', 'download', 'archivo', 'dea', 'href']):
        print(linea.strip()[:120])
