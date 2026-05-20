import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"

for js in ["/js/generales.js", "/js/busqueda.js", "/js/calendario.js"]:
    r = httpx.get(BASE + js, timeout=15)
    print(f"\n{'='*60}")
    print(f"=== {js} ({len(r.text)} chars) ===")
    print(f"{'='*60}")

    # Buscar strings que parezcan endpoints o rutas
    # Patrón 1: strings entre comillas simples o dobles
    strings = re.findall(r'["\']([^"\']{8,120})["\']', r.text)
    for s in strings:
        if any(k in s.lower() for k in [
            "aviso", "norma", "seccion", "pdf", "primera",
            "edicion", "json", "api", "fetch", "ajax", "url",
            "download", "anexo", "buscar", "lista", "get",
        ]):
            print(f"  {s}")

    # Patrón 2: llamadas $.ajax o fetch o axios
    ajax = re.findall(r'(?:url|href|fetch|ajax)\s*[:(]\s*["\']([^"\']+)["\']', r.text)
    if ajax:
        print("\n  -- AJAX/fetch calls --")
        for a in ajax:
            print(f"  {a}")