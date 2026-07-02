import httpx

BASE = "https://ajuste-production.up.railway.app"

endpoints = [
    "/api/v1/analisis/sector?sector=salud",
    "/api/v1/analisis/sector?sector=Ministerio de Salud",
    "/api/v1/analisis/sector?sector=80",
    "/api/v1/analisis/por-inciso",
    "/api/v1/partidas/?jurisdiccion_id=80&limit=3",
    "/api/v1/partidas/?limit=3",
    "/docs",
]

for ep in endpoints:
    try:
        r = httpx.get(BASE + ep, timeout=10)
        print(f"\n{'='*60}")
        print(f"GET {ep}")
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            text = r.text[:300]
        else:
            text = r.text[:200]
        print(text)
    except Exception as e:
        print(f"ERROR: {e}")