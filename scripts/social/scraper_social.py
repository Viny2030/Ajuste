"""
scraper_social.py
=================
Scraper de indicadores de impacto social del ajuste presupuestario argentino.

Fuentes oficiales:
  - DEIS / Ministerio de Salud : mortalidad infantil + adultos mayores
  - SNIC / Ministerio de Seguridad : suicidios

Salida:
  data/processed/social/indicadores_sociales.json   ← datos para el dashboard
  data/processed/social/metadata.json               ← checksums para detección de cambios
"""

import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR   = REPO_ROOT / "data" / "processed" / "social"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DATA_FILE = OUT_DIR / "indicadores_sociales.json"
META_FILE = OUT_DIR / "metadata.json"

# ── URLs fuentes oficiales ─────────────────────────────────────────────────────
SOURCES = {
    "deis_publicaciones" : "https://www.argentina.gob.ar/salud/deis/publicaciones",
    "deis_datos_abiertos": "https://datos.salud.gob.ar/dataset/tasa-de-mortalidad-infantil",
    "snic_suicidios"     : "https://www.argentina.gob.ar/seguridad/estadisticascriminales/datos",
}

# ── Datos base (dic-2023, punto de partida del ajuste) ────────────────────────
# Se actualizan cuando el scraper encuentra valores nuevos en las fuentes.
# Si no hay datos nuevos disponibles aún, se sirven estos valores estáticos
# con una nota de "último dato disponible".

BASELINE = {
    "mortalidad_infantil": {
        "anio": 2023,
        "tasa_por_mil": 8.0,
        "muertes_absolutas": 3689,
        "nacidos_vivos": 460902,
        "componente_neonatal": 5.5,
        "componente_posneonatal": 2.5,
    },
    "mortalidad_adultos_mayores": {
        "anio": 2023,
        "muertes_65_mas": 224000,       # estimado DEIS/ISALUD
        "muertes_totales": 322000,      # estimado
        "tasa_variacion_pct": None,
    },
    "suicidios": {
        "anio": 2023,
        "casos": 4205,
        "tasa_por_100k": 9.0,
    },
}

ULTIMO_DATO_CONOCIDO = {
    "mortalidad_infantil": {
        "anio": 2024,
        "tasa_por_mil": 8.5,
        "muertes_absolutas": 3513,
        "nacidos_vivos": 413135,
        "componente_neonatal": 6.0,
        "componente_posneonatal": 2.5,
        "var_absoluta_tasa": 0.5,
        "var_pct_tasa": 6.25,
        "fuente": "DEIS – Anuario Estadísticas Vitales 2024",
        "url_fuente": "https://www.argentina.gob.ar/salud/deis/publicaciones",
    },
    "mortalidad_adultos_mayores": {
        "anio": 2024,
        "muertes_65_mas": 245276,       # estimado DEIS/ISALUD
        "muertes_totales": 345000,      # estimado
        "exceso_muertes_65_mas": 21276,
        "var_pct_65_mas": 9.5,
        "var_pct_total": 7.1,
        "desglose": {
            "65_69": {"var_pct": 7.0},
            "75_84": {"var_pct": 8.0},
            "85_mas": {"var_pct": 10.0},
        },
        "causas_principales": [
            {"causa": "Neumonía e influenza", "var_pct": 16},
            {"causa": "Septicemias",           "var_pct": 11},
            {"causa": "Enfermedades del corazón", "var_pct": 8},
            {"causa": "Cerebrovasculares",     "var_pct": 6},
        ],
        "fuente": "DEIS – Anuario Estadísticas Vitales 2024 / ISALUD",
        "url_fuente": "https://www.argentina.gob.ar/salud/deis/publicaciones",
    },
    "suicidios": {
        "anio": 2024,
        "casos": 4249,
        "tasa_por_100k": 9.8,
        "var_absoluta_casos": 44,
        "var_pct_casos": 1.0,
        "var_absoluta_tasa": 0.8,
        "var_pct_tasa": 8.9,
        "es_record_historico": True,
        "principal_causa_muerte_violenta": True,
        "intentos_notificados_2023_2025": 15807,
        "promedio_intentos_diarios": 22,
        "fuente": "SNIC – Informe Ejecutivo 2024, Ministerio de Seguridad",
        "url_fuente": "https://www.argentina.gob.ar/seguridad/estadisticascriminales/datos",
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256_url(url: str, timeout: int = 20) -> str | None:
    """Descarga la URL y devuelve el SHA-256 de su contenido."""
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "MAP-Social-Scraper/1.0"})
        r.raise_for_status()
        return hashlib.sha256(r.content).hexdigest()
    except Exception as e:
        log.warning(f"No se pudo checkear {url}: {e}")
        return None


def cargar_metadata() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {}


def guardar_metadata(meta: dict) -> None:
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def cargar_datos_actuales() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


# ── Detección de cambios en fuentes ───────────────────────────────────────────

def detectar_cambios() -> dict[str, bool]:
    """
    Compara el SHA-256 actual de cada fuente con el guardado en metadata.json.
    Devuelve {nombre_fuente: hubo_cambio}.
    """
    meta_anterior = cargar_metadata()
    meta_nueva    = {}
    cambios       = {}

    for nombre, url in SOURCES.items():
        log.info(f"Verificando cambios en: {nombre}")
        nuevo_hash = sha256_url(url)
        viejo_hash = meta_anterior.get(nombre, {}).get("hash")

        cambio = nuevo_hash is not None and nuevo_hash != viejo_hash
        cambios[nombre] = cambio

        meta_nueva[nombre] = {
            "url"           : url,
            "hash"          : nuevo_hash or viejo_hash,
            "ultima_revision": datetime.now(timezone.utc).isoformat(),
            "cambio_detectado": cambio,
        }

        if cambio:
            log.info(f"  → CAMBIO DETECTADO en {nombre}")
        else:
            log.info(f"  → Sin cambios en {nombre}")

    guardar_metadata(meta_nueva)
    return cambios


# ── Parsers de fuentes ─────────────────────────────────────────────────────────

def parsear_deis() -> dict | None:
    """
    Intenta extraer la tasa de mortalidad infantil del último anuario DEIS.
    Si no puede parsear, devuelve None y se usan los datos conocidos.
    """
    url = SOURCES["deis_publicaciones"]
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "MAP-Social-Scraper/1.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Buscar links a anuarios PDF
        pdf_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if "anuario" in a["href"].lower() or "estadisticas-vitales" in a["href"].lower()
        ]

        if pdf_links:
            log.info(f"DEIS: encontrados {len(pdf_links)} links de anuarios")
            # Año más reciente mencionado en los links
            anios = re.findall(r"20\d{2}", " ".join(pdf_links))
            if anios:
                anio_max = max(int(a) for a in anios)
                log.info(f"DEIS: año más reciente detectado en links: {anio_max}")
                if anio_max > ULTIMO_DATO_CONOCIDO["mortalidad_infantil"]["anio"]:
                    log.info(f"DEIS: hay datos más nuevos disponibles (año {anio_max}) — requiere parseo manual del PDF")
                    return {"nuevo_anio_detectado": anio_max, "url_pdf": pdf_links[0]}

        return None  # Sin datos nuevos parseables automáticamente

    except Exception as e:
        log.warning(f"Error parseando DEIS: {e}")
        return None


def parsear_snic() -> dict | None:
    """
    Verifica si hay nuevos informes SNIC disponibles.
    """
    url = SOURCES["snic_suicidios"]
    try:
        r = httpx.get(url, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "MAP-Social-Scraper/1.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        pdf_links = [
            a["href"] for a in soup.find_all("a", href=True)
            if "snic" in a["href"].lower() or "suicidio" in a["href"].lower()
        ]

        if pdf_links:
            anios = re.findall(r"20\d{2}", " ".join(pdf_links))
            if anios:
                anio_max = max(int(a) for a in anios)
                log.info(f"SNIC: año más reciente detectado: {anio_max}")
                if anio_max > ULTIMO_DATO_CONOCIDO["suicidios"]["anio"]:
                    return {"nuevo_anio_detectado": anio_max, "url_pdf": pdf_links[0]}

        return None

    except Exception as e:
        log.warning(f"Error parseando SNIC: {e}")
        return None


# ── Constructor del JSON de salida ─────────────────────────────────────────────

def construir_payload(
    cambios: dict[str, bool],
    nuevo_deis: dict | None,
    nuevo_snic: dict | None,
) -> dict:
    """
    Construye el JSON final con variaciones absolutas y % desde dic-2023.
    """
    ahora = datetime.now(timezone.utc).isoformat()

    # Partimos de los últimos datos conocidos
    infantil = dict(ULTIMO_DATO_CONOCIDO["mortalidad_infantil"])
    mayores  = dict(ULTIMO_DATO_CONOCIDO["mortalidad_adultos_mayores"])
    suicidios = dict(ULTIMO_DATO_CONOCIDO["suicidios"])

    # Si DEIS detectó datos más nuevos, registrar alerta
    alerta_nuevos_datos = []
    if nuevo_deis and "nuevo_anio_detectado" in nuevo_deis:
        alerta_nuevos_datos.append({
            "fuente": "DEIS",
            "anio_nuevo": nuevo_deis["nuevo_anio_detectado"],
            "url": nuevo_deis.get("url_pdf"),
            "accion": "Requiere actualización manual del JSON con datos del PDF",
        })
    if nuevo_snic and "nuevo_anio_detectado" in nuevo_snic:
        alerta_nuevos_datos.append({
            "fuente": "SNIC",
            "anio_nuevo": nuevo_snic["nuevo_anio_detectado"],
            "url": nuevo_snic.get("url_pdf"),
            "accion": "Requiere actualización manual del JSON con datos del PDF",
        })

    return {
        "_meta": {
            "ultima_actualizacion"   : ahora,
            "periodo_base"           : "Diciembre 2023 (inicio gestión Milei)",
            "ultimo_dato_disponible" : "Anuario 2024 (DEIS) / Informe 2024 (SNIC)",
            "proxima_revision"       : "Anuario DEIS 2025 / SNIC 2025 (sin fecha de publicación)",
            "fuentes": {
                "deis": "https://www.argentina.gob.ar/salud/deis/publicaciones",
                "snic": "https://www.argentina.gob.ar/seguridad/estadisticascriminales/datos",
            },
            "cambios_detectados"     : cambios,
            "alertas_nuevos_datos"   : alerta_nuevos_datos,
        },
        "indicadores": {

            # ── 1. Mortalidad infantil ─────────────────────────────────────
            "mortalidad_infantil": {
                "descripcion"  : "Muertes de menores de 1 año por cada 1.000 nacidos vivos",
                "linea_de_base": {
                    "anio"                    : BASELINE["mortalidad_infantil"]["anio"],
                    "tasa_por_mil"            : BASELINE["mortalidad_infantil"]["tasa_por_mil"],
                    "muertes_absolutas"       : BASELINE["mortalidad_infantil"]["muertes_absolutas"],
                    "nacidos_vivos"           : BASELINE["mortalidad_infantil"]["nacidos_vivos"],
                    "componente_neonatal"     : BASELINE["mortalidad_infantil"]["componente_neonatal"],
                    "componente_posneonatal"  : BASELINE["mortalidad_infantil"]["componente_posneonatal"],
                },
                "ultimo_dato": infantil,
                "variacion_desde_base": {
                    "tasa": {
                        "absoluta"    : round(infantil["tasa_por_mil"] - BASELINE["mortalidad_infantil"]["tasa_por_mil"], 2),
                        "porcentual"  : infantil["var_pct_tasa"],
                        "direccion"   : "sube",
                    },
                    "componente_neonatal": {
                        "absoluta"    : round(infantil["componente_neonatal"] - BASELINE["mortalidad_infantil"]["componente_neonatal"], 2),
                        "porcentual"  : round((infantil["componente_neonatal"] - BASELINE["mortalidad_infantil"]["componente_neonatal"]) / BASELINE["mortalidad_infantil"]["componente_neonatal"] * 100, 1),
                        "direccion"   : "sube",
                    },
                    "nota": "La caída en muertes absolutas (-176) se explica por el derrumbe de la natalidad (-47.767 nacimientos). La tasa —que normaliza por nacidos vivos— sube de todas formas.",
                },
                "alertas": [
                    "Mayor aumento porcentual de la tasa desde 2002",
                    "Provincias críticas: Corrientes (7,5 → 14,0), Misiones (5,8 → 9,5), Entre Ríos (5,2 → 8,8)",
                ],
            },

            # ── 2. Mortalidad adultos mayores ──────────────────────────────
            "mortalidad_adultos_mayores": {
                "descripcion"  : "Muertes de personas de 65 años y más",
                "linea_de_base": {
                    "anio"           : BASELINE["mortalidad_adultos_mayores"]["anio"],
                    "muertes_65_mas" : BASELINE["mortalidad_adultos_mayores"]["muertes_65_mas"],
                    "muertes_totales": BASELINE["mortalidad_adultos_mayores"]["muertes_totales"],
                },
                "ultimo_dato": mayores,
                "variacion_desde_base": {
                    "muertes_65_mas": {
                        "absoluta"   : mayores["exceso_muertes_65_mas"],
                        "porcentual" : mayores["var_pct_65_mas"],
                        "direccion"  : "sube",
                    },
                    "muertes_totales": {
                        "absoluta"   : 23000,
                        "porcentual" : mayores["var_pct_total"],
                        "direccion"  : "sube",
                    },
                    "desglose_por_edad": mayores["desglose"],
                },
                "causas_principales_aumento": mayores["causas_principales"],
                "alertas": [
                    "21.276 muertes adicionales en mayores de 65 años vs. 2023",
                    "Los mayores de 85 años son el grupo más afectado (+10%)",
                    "Vinculado a discontinuación de medicamentos PAMI y recortes en cobertura",
                ],
            },

            # ── 3. Suicidios ───────────────────────────────────────────────
            "suicidios": {
                "descripcion"  : "Suicidios consumados registrados por SNIC",
                "linea_de_base": {
                    "anio"         : BASELINE["suicidios"]["anio"],
                    "casos"        : BASELINE["suicidios"]["casos"],
                    "tasa_por_100k": BASELINE["suicidios"]["tasa_por_100k"],
                },
                "ultimo_dato": suicidios,
                "variacion_desde_base": {
                    "casos": {
                        "absoluta"   : suicidios["var_absoluta_casos"],
                        "porcentual" : suicidios["var_pct_casos"],
                        "direccion"  : "sube",
                    },
                    "tasa": {
                        "absoluta"   : suicidios["var_absoluta_tasa"],
                        "porcentual" : suicidios["var_pct_tasa"],
                        "direccion"  : "sube",
                    },
                },
                "contexto": {
                    "vs_media_oms"             : "Tasa argentina (9,8) supera media global OMS (8,2)",
                    "principal_muerte_violenta": True,
                    "tendencia_decada"         : "Crecimiento del 27% desde 2014 (3.296 casos)",
                    "intentos_2023_2025"       : "15.807 intentos notificados (22/día promedio)",
                },
                "alertas": [
                    "Récord histórico absoluto en 2024",
                    "Primera causa de muerte violenta en Argentina desde 2023",
                    "Tasa supera por primera vez el promedio mundial OMS",
                ],
            },
        },
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    log.info("=" * 60)
    log.info("MAP Social Scraper — inicio")
    log.info("=" * 60)

    # 1. Detectar cambios en fuentes oficiales
    log.info("Paso 1: verificando cambios en fuentes oficiales...")
    cambios = detectar_cambios()
    hay_cambio = any(cambios.values())

    # 2. Intentar parsear datos nuevos si hubo cambio
    nuevo_deis = None
    nuevo_snic = None

    if cambios.get("deis_publicaciones") or cambios.get("deis_datos_abiertos"):
        log.info("Paso 2a: parseando DEIS (cambio detectado)...")
        nuevo_deis = parsear_deis()
    else:
        log.info("Paso 2a: DEIS sin cambios, saltando parseo.")

    if cambios.get("snic_suicidios"):
        log.info("Paso 2b: parseando SNIC (cambio detectado)...")
        nuevo_snic = parsear_snic()
    else:
        log.info("Paso 2b: SNIC sin cambios, saltando parseo.")

    # 3. Construir y guardar JSON
    log.info("Paso 3: construyendo payload...")
    payload = construir_payload(cambios, nuevo_deis, nuevo_snic)

    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(f"JSON guardado en: {DATA_FILE}")

    # 4. Reportar resultado para GitHub Actions
    if hay_cambio:
        log.info("RESULTADO: cambios detectados → commit se realizará.")
        print("CHANGED=true")
    else:
        log.info("RESULTADO: sin cambios en fuentes.")
        print("CHANGED=false")

    # Si hay alertas de datos nuevos, salir con código 2 (warning)
    alertas = payload["_meta"]["alertas_nuevos_datos"]
    if alertas:
        log.warning(f"ALERTA: {len(alertas)} fuente(s) con datos nuevos detectados:")
        for a in alertas:
            log.warning(f"  → {a['fuente']} año {a['anio_nuevo']}: {a['accion']}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
