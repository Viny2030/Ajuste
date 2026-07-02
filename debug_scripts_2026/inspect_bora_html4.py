import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"

r0 = httpx.get(BASE + "/seccion/primera/20240606", timeout=15, follow_redirects=True,
    headers={"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"})

ajax_headers = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Accept": "application/json, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/seccion/primera/20240606",
}

r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=ajax_headers, cookies=r0.cookies, timeout=15)
html = r.json()["html"]

# Ver bloque completo del aviso 308747 — buscar desde el div.linea-aviso anterior
# El link de anexos está al final del bloque, el título al principio
pos_anexo = html.find('detalleAviso/primera/308747/20240606?anexos=1')
# Buscar el inicio del bloque (div linea-aviso) hacia atrás
pos_inicio = html.rfind('<div class="linea-aviso">', 0, pos_anexo)
pos_fin = html.find('</div>', pos_anexo) + 100

bloque = html[pos_inicio:pos_fin + 200]
print("=== Bloque completo aviso 308747 ===")
print(bloque)

print("\n\n=== RAW 2000 chars antes del link de anexos ===")
print(html[max(0, pos_anexo-2000):pos_anexo+100])