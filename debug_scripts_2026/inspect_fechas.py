import httpx
import re

BASE = "https://www.boletinoficial.gob.ar"
HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0"}

# Probar con fechas conocidas de 2024
for fecha in ["20240606", "20240101", "20240201", "20240315"]:
    r = httpx.get(f"{BASE}/seccion/primera/{fecha}", timeout=15,
                  follow_redirects=True, headers=HEADERS)
    print(f"\nGET /seccion/primera/{fecha} → {r.status_code} (final: {r.url})")

    # Extraer todas las listas de fechas del JSON embebido
    m = re.search(r'"fechas":\[([^\]]+)\]', r.text)
    if m:
        fechas = [f.strip().strip('"') for f in m.group(1).split(',')]
        print(f"  fechas encontradas: {len(fechas)}")
        print(f"  rango: {fechas[0]} → {fechas[-1]}")
        # Ver si junio 2024 está incluido
        junio = [f for f in fechas if f.startswith("202406")]
        print(f"  fechas junio 2024: {junio}")
    else:
        print("  No se encontró JSON de fechas")
        # Ver si hay algún script inline con fechas
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
        for s in scripts:
            if 'fecha' in s.lower() and '2024' in s:
                print(f"  Script con fechas: {s[:300]}")
                break