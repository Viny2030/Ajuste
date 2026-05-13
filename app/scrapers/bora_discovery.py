# app/scrapers/bora_discovery.py
"""
Descubridor de Decisiones Administrativas presupuestarias.

FUENTE: Base Infoleg de Normativa Nacional (datos.jus.gob.ar)
  Dataset oficial del Ministerio de Justicia, actualizado mensualmente.
  Sin bloqueos, sin Playwright, sin dependencia del BORA que bloquea scraping.

Columnas del CSV:
  id_norma, tipo_norma, numero_norma, clase_norma, organismo_origen,
  fecha_sancion, numero_boletin, fecha_boletin, pagina_boletin,
  titulo_resumido, titulo_sumario, texto_resumido, observaciones,
  texto_original, texto_actualizado, modificada_por, modifica_a
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

# ── Filtros de clasificación ──────────────────────────────────────────────────

# El titulo_sumario debe contener alguna de estas frases para ser candidata
_SUMARIO_PRESUP = [
    "presupuesto administracion nacional",
    "presupuesto general",
    "presupuesto nacional",
    "credito presupuestario",
    "crédito presupuestario",
    "modificacion presupuestaria",
    "modificación presupuestaria",
    "distribucion del presupuesto",
    "distribución del presupuesto",
]

# El texto_resumido debe contener alguna de estas frases para confirmar
_TEXTO_CONFIRMA = [
    "modificase el presupuesto",
    "modifícase el presupuesto",
    "ampliase el presupuesto",
    "amplíase el presupuesto",
    "reducese el presupuesto",
    "redúcese el presupuesto",
    "distribucion de creditos",
    "distribución de créditos",
    "credito presupuestario",
    "crédito presupuestario",
]

# Palabras que descartan la DA aunque pase el filtro anterior
_DESCARTE = [
    "designacion", "designación",
    "estructura organizativa",
    "planta de personal",
    "contratacion directa",
    "contratación directa",
    "licitacion", "licitación",
    "concurso",
    "regimen de contrataciones",
    "régimen de contrataciones",
    "transfierese",             # transferencia de agente (personal)
    "trasfierese",
]

# Organismos emisores válidos (JGM o Hacienda/Economía que firman junto al JGM)
_ORGANISMOS_OK = [
    "jefatura de gabinete",
    "ministerio de economia",
    "ministerio de economía",
    "secretaria de hacienda",
    "secretaría de hacienda",
]

VERBOS_REDUCCION  = ["suprimase","suprímase","reduzcanse","redúzcase","disminúyase",
                     "eliminase","elimínase","reduccion","reducción"]
VERBOS_AMPLIACION = ["ampliase","amplíase","incrementese","increméntese","autorizase",
                     "autorízase","ampliacion","ampliación","refuerzo"]
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
    """
    Determina si una DA es una modificación presupuestaria real.

    Estrategia:
    1. titulo_sumario debe mencionar presupuesto explícitamente
    2. texto_resumido debe confirmar que modifica el presupuesto
    3. Descartar si titulo_resumido o texto hablan de designaciones/contratos/agentes
    """
    sumario  = row.get("titulo_sumario", "").lower()
    resumido = row.get("titulo_resumido", "").lower()
    texto    = row.get("texto_resumido", "").lower()

    # Paso 1: el sumario debe mencionar presupuesto
    if not any(k in sumario for k in _SUMARIO_PRESUP):
        return False

    # Paso 2: el texto debe confirmar que modifica el presupuesto
    if not any(k in texto for k in _TEXTO_CONFIRMA):
        return False

    # Paso 3: descartar si hay señales de designaciones / transferencia de personal
    texto_completo = resumido + " " + texto
    if any(k in texto_completo for k in _DESCARTE):
        return False

    return True


# ── Descarga y caché ──────────────────────────────────────────────────────────

def _descargar_csv() -> str:
    """Descarga ZIP de Infoleg, guarda CSV en caché, retorna path."""
    logger.info("Descargando Infoleg normativa (%s)...", URL_INFOLEG_ZIP)
    with urllib.request.urlopen(URL_INFOLEG_ZIP, timeout=180) as resp:
        data = resp.read()
    logger.info("Descargado %.1f MB", len(data) / 1e6)
    zf = zipfile.ZipFile(io.BytesIO(data))
    nombre = next(n for n in zf.namelist() if n.endswith(".csv"))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        f.write(zf.read(nombre))
    logger.info("CSV guardado en %s", CACHE_PATH)
    return str(CACHE_PATH)


# ── API pública ───────────────────────────────────────────────────────────────

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

            numero   = row.get("numero_norma", "0").strip()
            anio     = fecha_str[:4]
            norma_id = f"DA-{numero}-{anio}"
            if norma_id in vistos:
                continue
            vistos.add(norma_id)

            texto_full = (
                row.get("titulo_resumido", "") + " " +
                row.get("titulo_sumario", "") + " " +
                row.get("texto_resumido", "")
            )

            url_texto   = row.get("texto_original", "")
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
                "numero_boletin": row.get("numero_boletin", ""),
                "pagina_boletin": row.get("pagina_boletin", ""),
                "organismo":      row.get("organismo_origen", ""),
                "titulo":         row.get("titulo_resumido", ""),
                "sumario":        row.get("titulo_sumario", ""),
                "texto_resumido": row.get("texto_resumido", "")[:1000],
                "tipo_accion":    _detectar_tipo_accion(texto_full),
                "url_infoleg":    url_infoleg,
                "url_bora":       (
                    f"https://www.boletinoficial.gob.ar/detalleAviso/primera/0"
                    f"/{row.get('numero_boletin', '')}"
                    f"/{fecha_str.replace('-', '')}"
                ),
                "pdf_hash":       _sha256(norma_id),
            })

    resultados.sort(key=lambda x: x["fecha_boletin"])
    logger.info(
        "Normas presupuestarias: %d (desde %s hasta %s)",
        len(resultados), desde_dt, hasta_dt,
    )
    return resultados


async def descargar_pdf_norma(
    url_norma: str,
    destino: str,
    fecha_boletin: str | None = None,  # formato YYYYMMDD, para fallback BORA
) -> str | None:
    """Descarga el PDF del anexo desde Infoleg. Retorna path local o None."""
    destino_path = Path(destino)
    if destino_path.exists() and destino_path.stat().st_size > 500:
        return destino

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Construir candidatos a PDF a partir de la URL de la norma
    candidates: list[str] = []
    if "infoleg" in url_norma:
        # Extraer id_norma de la URL si está
        m = re.search(r"id=(\d+)", url_norma)
        if m:
            id_n = m.group(1)
            base = int(id_n) // 10000 * 10000
            rango = f"{base}-{base + 9999}"
            candidates += [
                f"https://servicios.infoleg.gob.ar/infolegInternet/anexos/{rango}/{id_n}/norma.pdf",
                f"https://servicios.infoleg.gob.ar/infolegInternet/anexos/{rango}/{id_n}/texact.pdf",
            ]
    # Si la URL ya termina en .htm, probar variantes PDF
    if url_norma.endswith(".htm"):
        candidates += [
            url_norma.replace(".htm", ".pdf"),
            url_norma.replace("norma.htm", "texact.pdf"),
        ]

    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        # Intentar candidatos directos
        for pdf_url in candidates:
            try:
                resp = await client.get(pdf_url)
                ct = resp.headers.get("content-type", "")
                if resp.status_code == 200 and (
                    "pdf" in ct or pdf_url.endswith(".pdf")
                ) and len(resp.content) > 1000:
                    destino_path.parent.mkdir(parents=True, exist_ok=True)
                    destino_path.write_bytes(resp.content)
                    logger.info("PDF OK: %s → %s", pdf_url, destino)
                    return destino
            except Exception as e:
                logger.debug("PDF candidate falló %s: %s", pdf_url, e)

        # Fallback 1: buscar link PDF en la página HTML de Infoleg
        try:
            resp = await client.get(url_norma, timeout=20)
            if resp.status_code == 200:
                base_url = url_norma.rsplit("/", 1)[0] + "/"
                for match in re.findall(r'href=["\']([^"\']*\.pdf[^"\']*)["\']', resp.text, re.I)[:5]:
                    if match.startswith("http"):
                        pdf_url = match
                    elif match.startswith("/"):
                        pdf_url = f"https://servicios.infoleg.gob.ar{match}"
                    else:
                        pdf_url = base_url + match
                    try:
                        r2 = await client.get(pdf_url, timeout=20)
                        if r2.status_code == 200 and len(r2.content) > 1000:
                            destino_path.parent.mkdir(parents=True, exist_ok=True)
                            destino_path.write_bytes(r2.content)
                            logger.info("PDF OK (HTML fallback): %s", pdf_url)
                            return destino
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("Fallback HTML Infoleg falló %s: %s", url_norma, e)

        # Fallback 2: BORA via POST base64
        # Busca el aviso presupuestario en la edición del BORA por fecha,
        # extrae el id_anexo y descarga via POST /pdf/download_anexo → {pdfBase64}
        try:
            import base64
            import json as _json

            # Obtener fecha del boletín desde parámetro, URL, o Infoleg
            fecha_bora: str | None = fecha_boletin  # YYYYMMDD preferido
            m_id = re.search(r"id=(\d+)", url_norma)

            # Intentar extraer fecha desde la URL de la norma si tiene patrón htm
            m_fecha = re.search(r"/(\d{8})/", url_norma)
            if m_fecha:
                fecha_bora = m_fecha.group(1)

            # Si no, necesitamos la fecha del boletin — se pasa via url_bora en el norma_data
            # Como no la tenemos acá, intentamos construirla desde el id_norma buscando en Infoleg
            if not fecha_bora and m_id:
                id_n = m_id.group(1)
                resp_i = await client.get(
                    f"https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={id_n}",
                    timeout=15,
                )
                # Buscar fecha en el HTML tipo "20240606" de 8 dígitos
                fechas = re.findall(r'\b(20\d{6})\b', resp_i.text)
                if fechas:
                    fecha_bora = fechas[0]

            if not fecha_bora:
                raise ValueError("No se pudo determinar la fecha del boletín")

            # Obtener primer id_aviso de esa fecha
            resp_sec = await client.get(
                f"https://www.boletinoficial.gob.ar/seccion/primera/{fecha_bora}",
                timeout=15,
            )
            id_avisos = re.findall(
                rf'detalleAviso/primera/(\d+)/{fecha_bora}',
                resp_sec.text,
            )
            if not id_avisos:
                raise ValueError(f"No se encontraron avisos para fecha {fecha_bora}")

            primer_id = int(id_avisos[0])

            # Iterar avisos hasta encontrar el presupuestario (máx 50)
            for id_av in range(primer_id, primer_id + 50):
                bora_url = f"https://www.boletinoficial.gob.ar/detalleAviso/primera/{id_av}/{fecha_bora}"
                resp_b = await client.get(bora_url, timeout=10)
                if resp_b.status_code != 200:
                    continue
                if "descargarPDFAnexo" not in resp_b.text:
                    continue
                if "presupuest" not in resp_b.text.lower():
                    continue

                # Extraer nro_anexo e id_anexo
                # Formato: descargarPDFAnexo("primera","1", "7136144", "20240606", ...)
                anexo_matches = re.findall(
                    r'descargarPDFAnexo\(\s*"primera"\s*,\s*"(\d+)"\s*,\s*"(\d+)"',
                    resp_b.text,
                )
                if not anexo_matches:
                    continue

                # Tomar el primer anexo (Art 1° — el de modificación de créditos)
                nro_anexo, id_anexo = anexo_matches[0]

                bora_headers = {
                    **headers,
                    "Referer": bora_url,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                }
                resp_pdf = await client.post(
                    "https://www.boletinoficial.gob.ar/pdf/download_anexo",
                    data={
                        "seccion": "primera",
                        "nroAnexo": nro_anexo,
                        "idAnexo": id_anexo,
                        "fechaPublicacion": fecha_bora,
                    },
                    headers=bora_headers,
                    timeout=60,
                )
                if resp_pdf.status_code == 200:
                    data = resp_pdf.json()
                    pdf_bytes = base64.b64decode(data["pdfBase64"])
                    if len(pdf_bytes) > 1000:
                        destino_path.parent.mkdir(parents=True, exist_ok=True)
                        destino_path.write_bytes(pdf_bytes)
                        logger.info(
                            "PDF OK (BORA base64): id_aviso=%s anexo=%s fecha=%s",
                            id_av, id_anexo, fecha_bora,
                        )
                        return destino
                break  # encontramos el aviso pero falló el POST

        except Exception as e:
            logger.debug("Fallback BORA base64 falló: %s", e)

    logger.warning("No se pudo descargar PDF: %s", url_norma)
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Descubridor Infoleg — DAs presupuestarias")
    p.add_argument("--desde", default="01/01/2023")
    p.add_argument("--hasta", default=None)
    p.add_argument("--forzar", action="store_true", help="Forzar re-descarga del ZIP")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    normas = buscar_normas(
        desde=args.desde, hasta=args.hasta, forzar_descarga=args.forzar
    )

    print(f"\n{'─'*95}")
    print(f"{'Fecha':12}  {'DA':15}  {'Tipo':14}  {'Organismo':30}  {'Título'}")
    print(f"{'─'*95}")
    for n in normas:
        print(
            f"{n['fecha_boletin']:12}  "
            f"DA-{n['numero']:>5}/{n['anio']}  "
            f"{n['tipo_accion']:14}  "
            f"{n['organismo'][:30]:30}  "
            f"{n['titulo'][:35]}"
        )
    print(f"{'─'*95}")
    print(f"Total: {len(normas)} DAs presupuestarias\n")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(normas, f, ensure_ascii=False, indent=2)
        print(f"Guardado: {args.output}")