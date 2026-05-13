# app/scrapers/bora_discovery.py
"""
Descubridor de Decisiones Administrativas presupuestarias.

FUENTE: Base Infoleg de Normativa Nacional (datos.jus.gob.ar)
  Dataset oficial del Ministerio de Justicia, actualizado mensualmente.
  Sin bloqueos, sin Playwright, sin dependencia del BORA que bloquea scraping.

El BORA bloquea todas las llamadas programáticas (error 2, 503, 403).
Infoleg tiene el mismo contenido en CSV público y descargable.
"""

import csv
import hashlib
import io
import logging
import re
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

URL_INFOLEG_ZIP = (
    "https://datos.jus.gob.ar/dataset/d9a963ea-8b1d-4ca3-9dd9-07a4773e8c23"
    "/resource/bf0ec116-ad4e-4572-a476-e57167a84403"
    "/download/base-infoleg-normativa-nacional.zip"
)
CACHE_PATH = Path("data/processed/infoleg_normativa.csv")

VERBOS_PRESUP = [
    "presupuest","credito","crédito","modificac",
    "distributivo","reduc","amplia","reasign",
    "decreto 88","ley 27",
]
VERBOS_REDUCCION  = ["suprimase","suprímase","reduzcanse","redúzcase","disminúyase","eliminase","elimínase","reduccion","reducción"]
VERBOS_AMPLIACION = ["ampliase","amplíase","incrementese","increméntese","autorizase","autorízase","ampliacion","ampliación","refuerzo"]
VERBOS_REASIGN    = ["transfierase","transfiérase","reasignese","reasígnese","redistribúyase"]


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _detectar_tipo_accion(texto: str) -> str:
    t = texto.lower()
    if any(v in t for v in VERBOS_REDUCCION):  return "REDUCCION"
    if any(v in t for v in VERBOS_REASIGN):    return "REASIGNACION"
    if any(v in t for v in VERBOS_AMPLIACION): return "AMPLIACION"
    return "MODIFICACION"


def _es_presupuestaria(row: dict) -> bool:
    texto = (row.get("titulo_resumido","") + " " +
             row.get("titulo_sumario","") + " " +
             row.get("texto_resumido","")).lower()
    return any(v in texto for v in VERBOS_PRESUP)


def _descargar_csv() -> str:
    """Descarga ZIP de Infoleg, guarda CSV en caché, retorna path."""
    logger.info("Descargando Infoleg normativa (%s)...", URL_INFOLEG_ZIP)
    with urllib.request.urlopen(URL_INFOLEG_ZIP, timeout=180) as resp:
        data = resp.read()
    logger.info("Descargado %.1f MB", len(data)/1e6)
    zf = zipfile.ZipFile(io.BytesIO(data))
    nombre = next(n for n in zf.namelist() if n.endswith(".csv"))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        f.write(zf.read(nombre))
    logger.info("CSV guardado en %s", CACHE_PATH)
    return str(CACHE_PATH)


def buscar_normas(
    desde: str = "01/01/2023",
    hasta: str | None = None,
    forzar_descarga: bool = False,
) -> list[dict]:
    """
    Retorna DAs presupuestarias desde `desde` hasta `hasta`.
    Usa caché local si existe y forzar_descarga=False.
    """
    try:
        desde_dt = datetime.strptime(desde, "%d/%m/%Y").date()
    except ValueError:
        desde_dt = datetime(2023, 1, 1).date()
    hasta_dt = (
        datetime.strptime(hasta, "%d/%m/%Y").date()
        if hasta else datetime.today().date()
    )

    csv_path = (
        _descargar_csv()
        if forzar_descarga or not CACHE_PATH.exists()
        else str(CACHE_PATH)
    )

    resultados = []
    vistos: set[str] = set()

    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("tipo_norma") != "Decisión Administrativa":
                continue
            fecha_str = row.get("fecha_boletin", "")
            try:
                fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if not (desde_dt <= fecha_dt <= hasta_dt):
                continue
            if not _es_presupuestaria(row):
                continue

            numero = row.get("numero_norma", "0").strip()
            anio   = fecha_str[:4]
            norma_id = f"DA-{numero}-{anio}"
            if norma_id in vistos:
                continue
            vistos.add(norma_id)

            texto_full = (row.get("titulo_resumido","") + " " +
                          row.get("titulo_sumario","") + " " +
                          row.get("texto_resumido",""))

            url_texto = row.get("texto_original", "")
            url_infoleg = (
                url_texto if url_texto.startswith("http")
                else f"https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={row['id_norma']}"
            )

            resultados.append({
                "norma_id":       norma_id,
                "tipo_norma":     "DA",
                "numero":         numero,
                "anio":           int(anio),
                "fecha_boletin":  fecha_str,
                "numero_boletin": row.get("numero_boletin",""),
                "pagina_boletin": row.get("pagina_boletin",""),
                "organismo":      row.get("organismo_origen",""),
                "titulo":         row.get("titulo_resumido",""),
                "sumario":        row.get("titulo_sumario",""),
                "texto_resumido": row.get("texto_resumido","")[:1000],
                "tipo_accion":    _detectar_tipo_accion(texto_full),
                "url_infoleg":    url_infoleg,
                "url_bora":       (
                    f"https://www.boletinoficial.gob.ar/seccion/primera/0"
                    f"/{row.get('numero_boletin','')}"
                    f"/{fecha_str.replace('-','')}"
                ),
                "pdf_hash":       _sha256(norma_id),
            })

    resultados.sort(key=lambda x: x["fecha_boletin"])
    logger.info("Normas: %d (desde %s hasta %s)", len(resultados), desde_dt, hasta_dt)
    return resultados


async def descargar_pdf_norma(url_norma: str, destino: str) -> str | None:
    """Descarga el PDF del anexo desde Infoleg. Retorna path local o None."""
    destino_path = Path(destino)
    if destino_path.exists():
        return destino

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    candidates = []
    if url_norma.endswith(".htm"):
        candidates.append(url_norma.replace(".htm", ".pdf"))
        candidates.append(url_norma.replace("norma.htm", "texact.pdf"))

    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        for pdf_url in candidates:
            try:
                resp = await client.get(pdf_url)
                ct = resp.headers.get("content-type","")
                if resp.status_code == 200 and ("pdf" in ct or pdf_url.endswith(".pdf")):
                    destino_path.parent.mkdir(parents=True, exist_ok=True)
                    destino_path.write_bytes(resp.content)
                    logger.info("PDF OK: %s", pdf_url)
                    return destino
            except Exception as e:
                logger.debug("PDF candidate falló %s: %s", pdf_url, e)

        # fallback: buscar link en HTML
        try:
            resp = await client.get(url_norma)
            if resp.status_code == 200:
                for match in re.findall(r'href="([^"]*\.pdf[^"]*)"', resp.text, re.I)[:3]:
                    pdf_url = match if match.startswith("http") else f"https://servicios.infoleg.gob.ar{match}"
                    try:
                        r2 = await client.get(pdf_url)
                        if r2.status_code == 200:
                            destino_path.parent.mkdir(parents=True, exist_ok=True)
                            destino_path.write_bytes(r2.content)
                            logger.info("PDF OK (HTML): %s", pdf_url)
                            return destino
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("Fallback HTML falló %s: %s", url_norma, e)

    logger.warning("No se pudo descargar PDF: %s", url_norma)
    return None


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--desde", default="01/01/2023")
    p.add_argument("--hasta", default=None)
    p.add_argument("--forzar", action="store_true")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    normas = buscar_normas(desde=args.desde, hasta=args.hasta, forzar_descarga=args.forzar)
    print(f"\n{'─'*90}")
    print(f"{'Fecha':12}  {'DA':12}  {'Tipo':14}  {'Título'}")
    print(f"{'─'*90}")
    for n in normas:
        print(f"{n['fecha_boletin']:12}  DA-{n['numero']:>5}/{n['anio']}  {n['tipo_accion']:14}  {n['titulo'][:45]}")
    print(f"{'─'*90}")
    print(f"Total: {len(normas)} DAs presupuestarias\n")
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(normas, f, ensure_ascii=False, indent=2)
        print(f"Guardado: {args.output}")