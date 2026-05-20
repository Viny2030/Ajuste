import asyncio, httpx, re, json

async def test():
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get("https://www.boletinoficial.gob.ar/seccion/primera/20240102")
        cookies = dict(r.cookies)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.boletinoficial.gob.ar/seccion/primera/20240102"
        }
        r2 = await c.get(
            "https://www.boletinoficial.gob.ar/calendario/dias_publicacion/2024/primera",
            cookies=cookies, headers=headers
        )
        print("RAW:", r2.text[:200])
        # El servidor devuelve un string JSON que contiene otro JSON con &quot;
        raw = r2.text.replace('&quot;', '"')
        # Primer parse: desenvuelve las comillas externas si es string
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        print(f"Type: {type(parsed)}")
        print(f"Keys: {list(parsed.keys()) if isinstance(parsed, dict) else 'not a dict'}")
        fechas = parsed.get("fechas", []) if isinstance(parsed, dict) else []
        print(f"Total fechas 2024: {len(fechas)}")
        print(f"Primeras: {fechas[:3]}")
        print(f"Ultimas: {fechas[-3:]}")

asyncio.run(test())