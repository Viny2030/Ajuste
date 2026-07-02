import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"
HEADERS_HTML = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}
HEADERS_AJAX = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Accept": "application/json, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/seccion/primera/20240606",
}

r0 = httpx.get(BASE + "/seccion/primera/20240606", timeout=15,
               follow_redirects=True, headers=HEADERS_HTML)
r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=HEADERS_AJAX, cookies=r0.cookies, timeout=15)
html = r.json()["html"]

fecha_bora_str = "20240606"
id_aviso = "308747"

# Posición del link principal (sin ?anexos)
patron_link = f'href="/detalleAviso/primera/{id_aviso}/{fecha_bora_str}"'
pos = html.find(patron_link)
print(f"Link principal pos={pos}")

# Posición del div linea-aviso después del link
pos_div = html.find('<div class="linea-aviso">', pos)
print(f"linea-aviso pos={pos_div}")
print(f"Distancia link→linea-aviso: {pos_div - pos} chars")
print(f"(el parser permite max 200)")

# Ver el bloque completo
pos_fin = html.find('</div>', pos_div)
bloque = html[pos_div:pos_fin+10]
print(f"\nBloque linea-aviso:")
print(bloque)

# Extraer con los patrones del parser
items = re.findall(r'<p class="item">([^<]+)</p>', bloque)
smalls = re.findall(r'<small>([^<]+)</small>', bloque)
print(f"\nitems: {items}")
print(f"smalls: {smalls}")