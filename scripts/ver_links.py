import httpx, re

url = "http://servicios.infoleg.gob.ar/infolegInternet/anexos/395000-399999/399031/norma.htm"
r = httpx.get(url, follow_redirects=True, timeout=15)
print("Status:", r.status_code)
# Buscar todos los links
links = re.findall(r'href=["\']([^"\']+)["\']', r.text, re.I)
for l in links[:20]:
    print(" ", l)
