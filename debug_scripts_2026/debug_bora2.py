"""
Debug simple del BORA usando requests (sin Playwright).
Corre con: python debug_bora2.py
"""
import requests
import urllib.parse
from pathlib import Path

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}

# ── Test 1: búsqueda por URL directa ──────────────────────
print("=" * 60)
print("TEST 1: URL de búsqueda del BORA")
params = {
    "p_filtro": "Presupuesto",
    "p_fecha_desde": "01/01/2024",
    "p_fecha_hasta": "12/05/2026"
}
url1 = f"https://www.boletinoficial.gob.ar/seccion/primera?{urllib.parse.urlencode(params)}"
print(f"URL: {url1}")
try:
    r = requests.get(url1, headers=HEADERS, timeout=20)
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type', 'N/A')}")
    html = r.text
    Path("bora_test1.html").write_text(html, encoding="utf-8")
    print(f"HTML guardado ({len(html):,} chars)")
    # Buscar claves en el HTML
    for clave in ["item-norma", "norma", "aviso", "angular", "ng-app", "react", "No se encontr"]:
        count = html.lower().count(clave.lower())
        if count:
            print(f"  '{clave}' aparece {count} veces")
except Exception as e:
    print(f"ERROR: {e}")

# ── Test 2: API interna del BORA ──────────────────────────
print("\n" + "=" * 60)
print("TEST 2: API interna del BORA (endpoint JSON)")
api_urls = [
    "https://www.boletinoficial.gob.ar/norma/busqueda/primera?busqueda=Presupuesto&fechaDesde=20240101&fechaHasta=20260512",
    "https://www.boletinoficial.gob.ar/norma/busqueda?termino=Presupuesto&seccion=primera",
    "https://www.boletinoficial.gob.ar/buscar/norma?texto=Presupuesto&seccion=primera&desde=20240101",
]
for url in api_urls:
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
        print(f"\nURL: {url}")
        print(f"  Status: {r.status_code} | Content-Type: {r.headers.get('Content-Type','?')[:60]}")
        if r.status_code == 200:
            print(f"  Respuesta: {r.text[:300]}")
    except Exception as e:
        print(f"  ERROR: {e}")

# ── Test 3: Página principal del BORA ────────────────────
print("\n" + "=" * 60)
print("TEST 3: Página principal - ver si carga")
try:
    r = requests.get("https://www.boletinoficial.gob.ar", headers=HEADERS, timeout=15)
    print(f"Status: {r.status_code}")
    # Buscar scripts de SPA
    for fw in ["angular", "react", "vue", "ng-app", "__NEXT", "svelte"]:
        if fw.lower() in r.text.lower():
            print(f"  Framework detectado: {fw}")
    print(f"  Primeros 500 chars: {r.text[:500]}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n" + "=" * 60)
print("Archivos guardados: bora_test1.html")
print("Abrí bora_test1.html en el browser para ver el HTML crudo.")