# app/scrapers/pdf_check.py
"""
Validación mínima de PDFs descargados.

Antes se aceptaba cualquier respuesta de más de 1000 bytes: Infoleg y el BORA
devuelven a veces una página HTML (error, portada) con status 200, que se
guardaba como .pdf. pdfplumber después no encontraba tabla y la norma quedaba
marcada NORMATIVA para siempre (pasó con DA-24-2025 y DA-39-2025).
"""
from pathlib import Path


def es_pdf(contenido: bytes) -> bool:
    """True si el contenido empieza con la cabecera %PDF (el estándar la
    admite dentro del primer KB)."""
    return b"%PDF" in contenido[:1024]


def pdf_valido(ruta) -> bool:
    """True si el archivo existe y es un PDF (no un HTML guardado como .pdf)."""
    p = Path(ruta)
    try:
        if not p.is_file() or p.stat().st_size <= 500:
            return False
        with open(p, "rb") as f:
            return es_pdf(f.read(1024))
    except OSError:
        return False
