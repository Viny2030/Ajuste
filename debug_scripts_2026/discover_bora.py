# .github/scripts/discover_bora.py
"""
Script liviano para GitHub Actions — solo usa httpx, sin dependencias del proyecto.
Descubre DAs presupuestarias en el BORA de los últimos 30 días y guarda en
data/nuevas_das.json.

No requiere DB ni pdfplumber — solo scraping del listado del BORA.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

BORA_BASE = "https://www.boletinoficial.gob.ar"
HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "es-AR,es;q=0.9",
}
HEADERS_AJAX = {
    **HEADERS_HTML,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

_KEYWORDS_OK = ["presupuest", "crédito", "credito", "modificac", "reasign"]
_DESCARTE    = ["designacion", "designación", "estructura organizativa", "modulos", "módulos"]


def _limpiar(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


async def _obtener_fechas_anio(client: httpx.AsyncClient, anio: int, cookies: dict) -> list[str]:
    url = f"{BORA_BASE}/calendario/dias_publicacion/{anio}/primera"
    try:
        r = await client.get(url, cookies=cookies, headers=HEADERS_AJAX, timeout=15)
        raw = r.text.replace("&quot;", '"')
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return parsed.get("fechas", []) if isinstance(parsed, dict) else []
    except Exception:
        return []


async def _resolver_numero_da(client: httpx.AsyncClient, id_aviso: str, fecha_str: str, anio: str) -> tuple[str, str]:
    url = f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_str}"
    try:
        r = await client.get(url, headers=HEADERS_HTML, timeout=15)
        texto = r.text

        # No es una DA si contiene Ley NNNNN
        if re.search(r'\bley\s+\d+', texto[:500], re.IGNORECASE):
            return "", anio

        for pat in [
            r'Decisi[oó]n\s+Administrativa\s+N[°º]?\s*(\d+)[/\-](\d{4})',
            r'Decisi[oó]n\s+Administrativa\s+(\d+)/(\d{4})',
            r'\bDA-(\d{4})-(\d+)-',
            r'<title>[^<]*?(\d+)/(\d{4})',
        ]:
            m = re.search(pat, texto, re.IGNORECASE)
            if m:
                if 'DA-' in pat:
                    return m.group(2), m.group(1)
                return m.group(1), m.group(2)
    except Exception:
        pass
    return id_aviso, anio


async def _scraper_fecha(client: httpx.AsyncClient, fecha_str: str) -> list[dict]:
    try:
        r = await client.get(f"{BORA_BASE}/seccion/primera/{fecha_str}", headers=HEADERS_HTML, timeout=15)
        cookies = dict(r.cookies)
    except Exception:
        return []

    html_total = ""
    for pag in range(1, 6):
        try:
            r2 = await client.get(
                f"{BORA_BASE}/seccion/actualizar/primera",
                params={"pag": str(pag), "fecha": fecha_str},
                headers={**HEADERS_AJAX, "Referer": f"{BORA_BASE}/seccion/primera/{fecha_str}"},
                cookies=cookies,
                timeout=20,
            )
            data = r2.json()
            html_total += data.get("html", "")
            if not data.get("hay_mas_datos"):
                break
        except Exception:
            break
        await asyncio.sleep(0.2)

    if not html_total:
        return []

    anio = fecha_str[:4]
    resultados = []
    ids_con_anexo = set(re.findall(rf'detalleAviso/primera/(\d+)/{fecha_str}\?anexos=1', html_total))

    for id_aviso in ids_con_anexo:
        pos = html_total.find(f'href="/detalleAviso/primera/{id_aviso}/{fecha_str}"')
        if pos == -1:
            continue
        pos_div = html_total.find('<div class="linea-aviso">', pos)
        if pos_div == -1 or pos_div > pos + 500:
            continue
        bloque = html_total[pos_div:html_total.find('</div>', pos_div) + 10]

        items  = re.findall(r'<p class="item">([^<]+)</p>', bloque)
        smalls = re.findall(r'<small>([^<]+)</small>', bloque)
        titulo    = _limpiar(items[0])  if items          else ""
        norma_str = _limpiar(smalls[0]) if smalls         else ""
        sumario   = _limpiar(smalls[1]) if len(smalls) > 1 else ""

        texto = (titulo + " " + norma_str + " " + sumario).lower()
        if not any(k in texto for k in _KEYWORDS_OK):
            continue
        if any(k in texto for k in _DESCARTE):
            continue
        if re.search(r'\bley\s+\d+', texto):
            if "decision administrativa" not in texto and "da-" not in texto.replace(" ", ""):
                continue
        if "decisión administrativa" not in texto and "decision administrativa" not in texto and \
           "da-" not in texto.replace(" ", ""):
            if "presupuest" not in titulo.lower():
                continue

        # Resolver número
        numero, anio_da = None, anio
        for pat in [
            r'(?:Decisi[oó]n\s+Administrativa|D\.A\.)\s+N[°º]?\s*(\d+)[/\-](\d{4})',
            r'(?:Decisi[oó]n\s+Administrativa|D\.A\.)\s+(\d+)/(\d{4})',
        ]:
            m = re.search(pat, norma_str, re.IGNORECASE)
            if m:
                numero, anio_da = m.group(1), m.group(2)
                break
        if not numero:
            m = re.search(r'\bDA-(\d{4})-(\d+)-', sumario, re.IGNORECASE)
            if m:
                numero, anio_da = m.group(2), m.group(1)
        if not numero:
            numero, anio_da = await _resolver_numero_da(client, id_aviso, fecha_str, anio)
            if not numero or numero == id_aviso:
                continue  # no se pudo resolver, skip

        resultados.append({
            "norma_id":      f"DA-{numero}-{anio_da}",
            "numero":        numero,
            "anio":          int(anio_da),
            "fecha_boletin": f"{fecha_str[:4]}-{fecha_str[4:6]}-{fecha_str[6:]}",
            "titulo":        titulo or norma_str,
            "sumario":       sumario[:300],
            "id_aviso_bora": id_aviso,
            "url_bora":      f"{BORA_BASE}/detalleAviso/primera/{id_aviso}/{fecha_str}",
        })

    return resultados


async def main():
    # Rango: últimos 30 días (o desde DESDE env var)
    desde_env = os.environ.get("DESDE", "")
    if desde_env:
        try:
            desde_dt = datetime.strptime(desde_env, "%d/%m/%Y").date()
        except ValueError:
            desde_dt = date.today() - timedelta(days=30)
    else:
        desde_dt = date.today() - timedelta(days=30)

    hasta_dt = date.today()
    desde_str = desde_dt.strftime("%Y%m%d")
    hasta_str = hasta_dt.strftime("%Y%m%d")

    print(f"Buscando DAs entre {desde_dt} y {hasta_dt}...")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Obtener fechas habilitadas
        anios = list(range(desde_dt.year, hasta_dt.year + 1))
        fechas_habilitadas: set[str] = set()
        for anio in anios:
            r = await client.get(f"{BORA_BASE}/seccion/primera/{anio}0102", headers=HEADERS_HTML, timeout=15)
            cookies = dict(r.cookies)
            fechas = await _obtener_fechas_anio(client, anio, cookies)
            fechas_habilitadas.update(fechas)

        fechas_a_procesar = sorted(f for f in fechas_habilitadas if desde_str <= f <= hasta_str)
        print(f"Fechas a procesar: {len(fechas_a_procesar)}")

        resultados: dict[str, dict] = {}
        for fecha_str in fechas_a_procesar:
            avisos = await _scraper_fecha(client, fecha_str)
            for a in avisos:
                if a["norma_id"] not in resultados:
                    resultados[a["norma_id"]] = a
                    print(f"  ✅ {a['norma_id']} — {a['fecha_boletin']}")
            await asyncio.sleep(0.3)

    normas = sorted(resultados.values(), key=lambda x: x["fecha_boletin"])
    print(f"\nTotal: {len(normas)} DAs encontradas")

    # Guardar resultado
    output = Path("data/nuevas_das.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(normas, f, ensure_ascii=False, indent=2)
    print(f"Guardado: {output}")


if __name__ == "__main__":
    asyncio.run(main())