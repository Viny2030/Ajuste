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

# Bajar todas las páginas
html_total = ""
for pag in range(1, 10):
    r = httpx.get(BASE + "/seccion/actualizar/primera",
        params={"pag": str(pag), "fecha": "20240606"},
        headers=ajax_headers, cookies=r0.cookies, timeout=15)
    data = r.json()
    html_total += data.get("html", "")
    print(f"pag={pag} hay_mas={data.get('hay_mas_datos')} sig_pag={data.get('sig_pag')} html={len(data.get('html',''))}")
    if not data.get("hay_mas_datos"):
        break

print(f"\nHTML total acumulado: {len(html_total)} chars")

# Mostrar bloque completo de los avisos con anexos (presupuestarios)
for id_aviso in ["308747", "308748"]:
    pos = html_total.find(f"/detalleAviso/primera/{id_aviso}/")
    if pos == -1:
        continue
    bloque = html_total[max(0, pos-200):pos+600]
    print(f"\n{'='*60}")
    print(f"Aviso {id_aviso}:")
    print(bloque)

# Contar total avisos con anexos
anexos = re.findall(r'detalleAviso/primera/(\d+)/20240606\?anexos=1', html_total)
print(f"\nAvisos con anexos: {len(anexos)} — ids: {anexos}")

# Ver todos los <p class="item"> cerca de los anexos
for id_av in anexos:
    pos = html_total.find(f"/detalleAviso/primera/{id_av}/")
    bloque = html_total[max(0, pos-100):pos+400]
    items = re.findall(r'<p class="item[^"]*">([^<]+)', bloque)
    smalls = re.findall(r'<small>([^<]+)</small>', bloque)
    print(f"\n  id={id_av}")
    print(f"  items: {items}")
    print(f"  smalls: {smalls}")