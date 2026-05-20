import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"
HEADERS_HTML = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}
HEADERS_AJAX = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Accept": "application/json, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/seccion/primera/20240603",
}

# Obtener cookies desde la primera fecha del rango (igual que el scraper)
r0 = httpx.get(BASE + "/seccion/primera/20240603", timeout=15,
               follow_redirects=True, headers=HEADERS_HTML)
cookies = dict(r0.cookies)
print(f"Cookies desde 20240603: {list(cookies.keys())}")

# Obtener HTML de 20240606 con esas cookies
r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers={**HEADERS_AJAX, "Referer": BASE + "/seccion/primera/20240606"},
    cookies=cookies,
    timeout=15,
)
data = r.json()
html = data["html"]
print(f"HTML length: {len(html)}, hay_mas: {data.get('hay_mas_datos')}")

# Test del patron de búsqueda exacto del parser
fecha_bora_str = "20240606"

# Patrón 1: con comilla doble (")
pat1 = rf'detalleAviso/primera/(\d+)/{fecha_bora_str}\?anexos=1'
ids1 = re.findall(pat1, html)
print(f"\nPatrón ?anexos=1: {len(ids1)} matches → {ids1[:5]}")

# Ver exactamente cómo aparece en el HTML
pos = html.find("anexos=1")
print(f"\nContexto 'anexos=1' (pos={pos}):")
print(repr(html[max(0,pos-100):pos+50]))

# Buscar el link principal exacto
if ids1:
    id_aviso = ids1[0]
    # Patrón exacto del parser
    patron_link = rf'href="/detalleAviso/primera/{id_aviso}/{fecha_bora_str}"'
    pos2 = html.find(patron_link)
    print(f"\nBusco: {patron_link!r}")
    print(f"pos={pos2}")

    # Ver qué hay realmente en el HTML para ese id
    pos3 = html.find(f"detalleAviso/primera/{id_aviso}/")
    print(f"\nTodas las ocurrencias de detalleAviso/primera/{id_aviso}/:")
    start = 0
    while True:
        p = html.find(f"detalleAviso/primera/{id_aviso}/", start)
        if p == -1:
            break
        print(f"  pos={p}: {repr(html[p:p+80])}")
        start = p + 1