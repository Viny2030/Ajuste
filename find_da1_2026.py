import asyncio
import httpx
import re

BORA_BASE = "https://www.boletinoficial.gob.ar"
HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}
HEADERS_AJAX = {
    **HEADERS_HTML,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

async def main():
    fecha = "20260120"
    async with httpx.AsyncClient(follow_redirects=True) as c:
        # Obtener cookies
        r = await c.get(f"{BORA_BASE}/seccion/primera/{fecha}", headers=HEADERS_HTML)
        cookies = dict(r.cookies)

        # Obtener avisos del día
        html_total = ""
        for pag in range(1, 5):
            r2 = await c.get(
                f"{BORA_BASE}/seccion/actualizar/primera",
                params={"pag": str(pag), "fecha": fecha},
                headers={**HEADERS_AJAX, "Referer": f"{BORA_BASE}/seccion/primera/{fecha}"},
                cookies=cookies,
            )
            data = r2.json()
            html_total += data.get("html", "")
            if not data.get("hay_mas_datos"):
                break

        # Encontrar todos los avisos con ?anexos=1
        ids = re.findall(rf'detalleAviso/primera/(\d+)/{fecha}\?anexos=1', html_total)
        print(f"Avisos con anexo en {fecha}: {len(ids)}")

        # Para cada id, ver el título
        for id_av in ids:
            pos = html_total.find(f'href="/detalleAviso/primera/{id_av}/{fecha}"')
            if pos == -1:
                continue
            pos_div = html_total.find('<div class="linea-aviso">', pos)
            if pos_div == -1:
                continue
            pos_fin = html_total.find('</div>', pos_div)
            bloque = html_total[pos_div:pos_fin+10]
            items = re.findall(r'<p class="item">([^<]+)</p>', bloque)
            smalls = re.findall(r'<small>([^<]+)</small>', bloque)
            titulo = items[0].strip() if items else ""
            norma = smalls[0].strip() if smalls else ""
            sumario = smalls[1].strip() if len(smalls) > 1 else ""
            if any(k in (titulo + norma + sumario).lower() for k in ["presupuest", "decision administrativa", "da-"]):
                print(f"  id={id_av} | {titulo} | {norma} | {sumario[:80]}")

asyncio.run(main())