"""
scripts/reintentar_normativas.py
================================
Reintenta las normas que daily_sync marcó NORMATIVA porque NO pudo bajar el
PDF (hasta 2026-10 un fallo de descarga, aunque fuera transitorio, dejaba la
norma marcada para siempre).

Cómo se distinguen de las NORMATIVA legítimas: si en data/raw_pdfs/ hay un
PDF válido (cabecera %PDF), se bajó bien y la tabla vino vacía (NORMATIVA de
verdad); si no hay archivo, o es un HTML guardado como .pdf, fue una
descarga fallida.

Para cada descarga fallida: borra la norma y la vuelve a procesar con
app.core.daily_sync._procesar_norma. Si el PDF sigue sin bajar, la norma no
se vuelve a guardar y la próxima corrida de daily_sync la reintenta.

Uso:
  python -m scripts.reintentar_normativas            # sólo lista (no toca nada)
  python -m scripts.reintentar_normativas --aplicar  # reintenta
  python -m scripts.reintentar_normativas --aplicar --normas DA-24-2025 DA-39-2025
      # fuerza el reintento de esas normas aunque ya tengan PDF local
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.session import SessionLocal, DATABASE_URL  # noqa: E402
from app.database import models  # noqa: E402
from app.database.models import TIPO_DA_NORMATIVA  # noqa: E402
from app.core import daily_sync  # noqa: E402
from app.scrapers.pdf_check import pdf_valido  # noqa: E402


def _pdf_local(norma_id: str) -> Path:
    safe_id = norma_id.replace("/", "_").replace(" ", "_")
    return daily_sync.RAW_PDF_DIR / f"{safe_id}.pdf"


def descargas_fallidas(db) -> list:
    normas = db.query(models.NormaJGM).filter(models.NormaJGM.tipo_da == TIPO_DA_NORMATIVA).all()
    # Un HTML guardado como .pdf cuenta como descarga fallida.
    return [n for n in normas if not pdf_valido(_pdf_local(n.norma_id))]


def _como_norma_data(n) -> dict:
    """Reconstruye el dict que espera _procesar_norma a partir de la fila."""
    fecha = n.fecha_publicacion.strftime("%Y-%m-%d") if n.fecha_publicacion else ""
    return {
        "norma_id":      n.norma_id,
        "tipo_norma":    n.tipo_norma or "DA",
        "numero":        n.numero,
        "anio":          n.anio,
        "fecha_boletin": fecha,
        "titulo":        n.titulo or "",
        "url_infoleg":   n.pdf_url or "",
        # El link viejo de Infoleg (/detalleAviso/primera/0/...) no existía:
        # se usa la edición del día, igual que bora_discovery desde 2026-10.
        "url_bora":      f"https://www.boletinoficial.gob.ar/seccion/primera/{fecha.replace('-', '')}",
        "tipo_accion":   n.tipo_accion,
        "pdf_hash":      n.pdf_hash,
    }


async def reintentar(db, normas) -> dict:
    resultado = {}
    for n in normas:
        data = _como_norma_data(n)
        db.delete(n)
        db.commit()
        try:
            insertadas = await daily_sync._procesar_norma(data, db)
        except Exception as e:
            db.rollback()
            print(f"  ❌ {data['norma_id']}: {e}")
            insertadas = None
        guardada = db.query(models.NormaJGM).filter_by(norma_id=data["norma_id"]).first()
        resultado[data["norma_id"]] = (
            f"GASTO, {insertadas} partidas" if guardada and guardada.tipo_da != TIPO_DA_NORMATIVA
            else "NORMATIVA (PDF sin tabla)" if guardada
            else "sin PDF todavía: se reintentará en la próxima corrida"
        )
    return resultado


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="Reintentar (sin esto sólo lista)")
    ap.add_argument("--normas", nargs="+", default=None,
                    help="Reintentar estas normas NORMATIVA aunque tengan PDF local")
    args = ap.parse_args(argv)

    print(f"Base: {DATABASE_URL.split('@')[-1]}")
    db = SessionLocal()
    try:
        if args.normas:
            fallidas = (db.query(models.NormaJGM)
                        .filter(models.NormaJGM.norma_id.in_(args.normas),
                                models.NormaJGM.tipo_da == TIPO_DA_NORMATIVA).all())
        else:
            fallidas = descargas_fallidas(db)
        print(f"NORMATIVA por descarga fallida: {len(fallidas)} "
              f"{[n.norma_id for n in fallidas]}")
        if not fallidas or not args.aplicar:
            return 0
        for nid, estado in asyncio.run(reintentar(db, fallidas)).items():
            print(f"  {nid}: {estado}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
