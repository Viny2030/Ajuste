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

# Obtener cookies desde la fecha correcta (misma sesión)
r0 = httpx.get(BASE + "/seccion/primera/20240606", timeout=15,
               follow_redirects=True, headers=HEADERS_HTML)
print(f"Sesión desde 20240606: {r0.status_code}, cookies: {list(r0.cookies.keys())}")

# Llamar al endpoint AJAX con esas cookies
r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=HEADERS_AJAX,
    cookies=r0.cookies,
    timeout=15,
)
data = r.json()
html = data["html"]

print(f"\nhay_mas_datos: {data.get('hay_mas_datos')}")
print(f"HTML length: {len(html)}")

# Buscar todos los ?anexos=1
anexos = re.findall(r'detalleAviso/primera/(\d+)/(\d+)\?anexos=1', html)
print(f"\nAnexos encontrados: {len(anexos)}")
for id_av, fecha in anexos[:5]:
    print(f"  id={id_av} fecha={fecha}")

# Ver bloque completo del primer aviso con anexo
if anexos:
    id_aviso, fecha_av = anexos[0]
    # Buscar el link SIN ?anexos (el principal)
    patron = f'/detalleAviso/primera/{id_aviso}/{fecha_av}"'
    pos = html.find(patron)
    print(f"\nLink principal para {id_aviso}: pos={pos}")
    if pos != -1:
        bloque = html[pos:pos+600]
        print(bloque)
    else:
        # Mostrar contexto alrededor del link con anexos
        pos2 = html.find(f'detalleAviso/primera/{id_aviso}/{fecha_av}?anexos=1')
        print(f"\nContexto alrededor del link con anexos (pos={pos2}):")
        print(html[max(0,pos2-100):pos2+1500])