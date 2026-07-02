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

# Cookies frescas para 20240606
r0 = httpx.get(BASE + "/seccion/primera/20240606", timeout=15,
               follow_redirects=True, headers=HEADERS_HTML)
cookies = dict(r0.cookies)

# HTML del AJAX
r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=HEADERS_AJAX, cookies=cookies, timeout=15)
html = r.json()["html"]
print(f"HTML: {len(html)} chars")

fecha_bora_str = "20240606"

# Test exacto del patrón del parser
pat = rf'detalleAviso/primera/(\d+)/{fecha_bora_str}\?anexos=1'
ids = re.findall(pat, html)
print(f"IDs con ?anexos=1: {ids[:5]}")

# Ver EXACTAMENTE cómo aparece en el HTML (raw bytes)
pos = html.find("?anexos=1")
print(f"\nContexto ?anexos=1 (raw):")
print(repr(html[max(0,pos-60):pos+20]))

# Verificar si hay diferencia de encoding
print(f"\nBuscar '?anexos' directamente: {html.count('?anexos')}")
print(f"Buscar 'anexos=1': {html.count('anexos=1')}")
print(f"Buscar 'anexos': {html.count('anexos')}")

# Ver el link principal para 308747
id_test = "308747"
pat2 = f'href="/detalleAviso/primera/{id_test}/{fecha_bora_str}"'
pos2 = html.find(pat2)
print(f"\nBusco link principal: {pat2!r}")
print(f"pos={pos2}")

# Ver todas las variantes del link para ese id
for variante in [
    f'primera/{id_test}/{fecha_bora_str}"',
    f'primera/{id_test}/{fecha_bora_str}',
    f'primera/{id_test}/',
]:
    pos3 = html.find(variante)
    if pos3 != -1:
        print(f"  Encontrado {variante!r} en pos={pos3}")
        print(f"  Contexto: {repr(html[pos3:pos3+100])}")