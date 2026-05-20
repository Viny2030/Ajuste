import httpx
import json

BASE = "https://www.boletinoficial.gob.ar"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.boletinoficial.gob.ar/seccion/primera/20240606",
    "X-Requested-With": "XMLHttpRequest",
}

# Endpoint 1: buscarRubro (carga inicial de avisos)
print("=== /seccion/buscarRubro ===")
r = httpx.post(
    BASE + "/seccion/buscarRubro",
    data={"seccion": "primera", "fecha": "20240606", "id_rubro": ""},
    headers=HEADERS,
    timeout=15,
    follow_redirects=False,
)
print(f"Status: {r.status_code}")
print(f"Content-Type: {r.headers.get('content-type')}")
print(r.text[:2000])

print("\n\n=== /seccion/actualizar/primera?pag=1 ===")
r2 = httpx.get(
    BASE + "/seccion/actualizar/primera?pag=1",
    params={"fecha": "20240606"},
    headers=HEADERS,
    timeout=15,
    follow_redirects=False,
)
print(f"Status: {r2.status_code}")
print(f"Content-Type: {r2.headers.get('content-type')}")
print(r2.text[:2000])

print("\n\n=== /busquedaAvanzada/realizarBusqueda ===")
r3 = httpx.post(
    BASE + "/busquedaAvanzada/realizarBusqueda",
    json={
        "terminos": "decision administrativa presupuesto",
        "organismos": "",
        "tipoNorma": "DA",
        "seccion": "primera",
        "desde": "20240601",
        "hasta": "20240630",
        "numeroPagina": 1,
    },
    headers={**HEADERS, "Content-Type": "application/json"},
    timeout=15,
    follow_redirects=False,
)
print(f"Status: {r3.status_code}")
print(f"Content-Type: {r3.headers.get('content-type')}")
print(r3.text[:2000])