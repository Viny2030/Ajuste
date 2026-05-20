import httpx
import re
import json

BASE = "https://www.boletinoficial.gob.ar"

# Obtener sesión
r0 = httpx.get(BASE + "/seccion/primera/20240606", timeout=15, follow_redirects=True,
    headers={"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"})

ajax_headers = {
    "User-Agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "Accept": "application/json, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE + "/seccion/primera/20240606",
}

r = httpx.get(BASE + "/seccion/actualizar/primera",
    params={"pag": "1", "fecha": "20240606"},
    headers=ajax_headers,
    cookies=r0.cookies,
    timeout=15,
)
data = r.json()
html = data["html"]
print(f"HTML total: {len(html)} chars")
print(f"hay_mas_datos: {data.get('hay_mas_datos')}")
print(f"sig_pag: {data.get('sig_pag')}")

# Buscar todos los links detalleAviso
avisos = re.findall(r'href="(/detalleAviso/primera/(\d+)/(\d+))"', html)
print(f"\nTotal avisos encontrados: {len(avisos)}")
print("Primeros 10 ids:", [a[1] for a in avisos[:10]])

# Ver estructura de un aviso completo — buscar el bloque alrededor del primer link
if avisos:
    id_aviso = avisos[0][1]
    pos = html.find(f"/detalleAviso/primera/{id_aviso}/")
    bloque = html[max(0, pos-500):pos+800]
    print(f"\n--- Bloque aviso id={id_aviso} ---")
    print(bloque)

# Buscar específicamente DAs presupuestarias
print("\n\n--- Avisos con 'presupuest' o 'Decisión' ---")
# Dividir por aviso
bloques = re.split(r'(?=<div[^>]*class="[^"]*aviso)', html)
for b in bloques:
    if 'presupuest' in b.lower() or ('decisi' in b.lower() and 'administr' in b.lower()):
        titulo_m = re.search(r'<[^>]*class="[^"]*titulo[^"]*"[^>]*>([^<]+)', b)
        org_m = re.search(r'<[^>]*class="[^"]*organismo[^"]*"[^>]*>([^<]+)', b)
        href_m = re.search(r'href="(/detalleAviso/[^"]+)"', b)
        print(f"  titulo: {titulo_m.group(1).strip() if titulo_m else '?'}")
        print(f"  org:    {org_m.group(1).strip() if org_m else '?'}")
        print(f"  href:   {href_m.group(1) if href_m else '?'}")
        print()