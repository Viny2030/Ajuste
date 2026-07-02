import httpx
import json
import re

BASE = "https://www.boletinoficial.gob.ar"

# Primero obtener una sesión válida cargando la página
session_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# Cargar la página para obtener cookies de sesión
r0 = httpx.get(
    BASE + "/seccion/primera/20240606",
    headers=session_headers,
    timeout=15,
    follow_redirects=True,
)
print(f"Página principal: {r0.status_code}")
print(f"Cookies: {dict(r0.cookies)}")

# Extraer dias habilitados del HTML (están embebidos como JSON)
m = re.search(r'"fechas":\[([^\]]+)\]', r0.text)
if m:
    fechas_raw = m.group(1).replace('"', '').split(',')
    print(f"\nFechas habilitadas 2024 (primeras 10): {fechas_raw[:10]}")
    print(f"Total fechas: {len(fechas_raw)}")

# Usar las cookies de sesión para llamar a buscarRubro
ajax_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/seccion/primera/20240606",
    "Origin": BASE,
}

# Test 1: POST con form data exacto del JS
# realizarBusquedaRubro('/seccion/buscarRubro', 'primera', '', '20240606')
print("\n=== Test 1: buscarRubro POST form ===")
r1 = httpx.post(
    BASE + "/seccion/buscarRubro",
    data={
        "seccion": "primera",
        "fecha": "20240606",
        "id_rubro": "",
        "ult_rubro": "",
        "numeroPagina": "1",
    },
    headers=ajax_headers,
    cookies=r0.cookies,
    timeout=15,
    follow_redirects=False,
)
print(f"Status: {r1.status_code}")
data = r1.json()
print(f"error: {data.get('error')}")
html = data.get("content", {}).get("html", "") if data.get("content") else ""
print(f"HTML length: {len(html)}")
print(f"sig_pag: {data.get('content', {}).get('sig_pag') if data.get('content') else 'N/A'}")
if html:
    # Mostrar primeros avisos
    titulos = re.findall(r'<[^>]*class="[^"]*titulo[^"]*"[^>]*>([^<]+)', html)
    print(f"Títulos encontrados: {titulos[:5]}")
    print(f"\nPrimeros 1000 chars del HTML:")
    print(html[:1000])

# Test 2: GET con fecha en URL
print("\n=== Test 2: actualizar con fecha en params ===")
r2 = httpx.get(
    BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=ajax_headers,
    cookies=r0.cookies,
    timeout=15,
    follow_redirects=False,
)
print(f"Status: {r2.status_code}")
data2 = r2.json()
html2 = data2.get("html", "")
print(f"HTML length: {len(html2)}")
if html2:
    print(html2[:1000])