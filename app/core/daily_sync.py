# app/core/daily_sync.py
"""
Pipeline de sincronización diaria:
  1. Scraper BORA → detecta nuevas normas JGM
  2. Descarga PDF de cada anexo nuevo
  3. pdf_processor → extrae tabla de partidas
  4. Persiste NormaJGM + ModificacionPresupuestaria en DB
  5. Invalida caché de MacroIndices (fuerza re-descarga del BCRA)

Uso:
  python -m app.core.daily_sync
"""
import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.session import SessionLocal, engine
from app.database import models
from app.scrapers.bora_discovery import BoraScraper, descargar_pdf_norma
from app.scrapers.pdf_processor import extraer_tabla_presupuesto
from app.core.engine import cargar_macro_indices

RAW_PDF_DIR = Path("data/raw_pdfs")
RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)


async def _procesar_norma(norma_data: dict, db: Session) -> int:
    """
    Persiste una norma y sus modificaciones de partidas.
    Retorna la cantidad de filas de modificaciones insertadas.
    """
    # 1. Dedup por norma_id
    existente = (
        db.query(models.NormaJGM)
        .filter(models.NormaJGM.norma_id == norma_data["norma_id"])
        .first()
    )
    if existente:
        return 0  # ya procesada

    # 2. Persistir NormaJGM
    try:
        fecha_pub = datetime.strptime(norma_data.get("fecha_publicacion", ""), "%d/%m/%Y")
    except Exception:
        fecha_pub = None

    norma_obj = models.NormaJGM(
        norma_id=norma_data["norma_id"],
        tipo_norma=norma_data.get("tipo_norma", "DA"),
        numero=norma_data.get("numero", ""),
        anio=int(norma_data.get("anio", datetime.now().year)),
        fecha_publicacion=fecha_pub,
        titulo=norma_data.get("titulo", ""),
        url_bora=norma_data.get("url_bora", ""),
        tipo_accion=norma_data.get("tipo_accion"),
        pdf_hash=norma_data.get("pdf_hash"),
    )
    db.add(norma_obj)
    db.flush()  # para tener el ID

    # 3. Descargar PDF del anexo
    pdf_path = RAW_PDF_DIR / f"{norma_data['norma_id'].replace('/', '_')}.pdf"
    if not pdf_path.exists():
        downloaded = await descargar_pdf_norma(norma_data["url_bora"], str(pdf_path))
    else:
        downloaded = str(pdf_path)

    if not downloaded:
        print(f"  ⚠️  Sin PDF para {norma_data['norma_id']} — solo se guardó la norma")
        db.commit()
        return 0

    # 4. Extraer tabla de partidas del PDF
    df_partidas = extraer_tabla_presupuesto(downloaded)
    if df_partidas is None or df_partidas.empty:
        print(f"  ⚠️  Tabla vacía en {norma_data['norma_id']}")
        db.commit()
        return 0

    # 5. Persistir modificaciones
    insertadas = 0
    total_reduccion = 0.0
    total_aumento = 0.0

    for _, fila in df_partidas.iterrows():
        programa_id = str(fila.get("programa_id", "")).strip()
        if not programa_id:
            continue

        reduccion = float(fila.get("reduccion", 0) or 0)
        aumento = float(fila.get("aumento", 0) or 0)
        monto_neto = aumento - reduccion

        mod = models.ModificacionPresupuestaria(
            norma_db_id=norma_obj.id,
            norma_id=norma_data["norma_id"],
            fecha_boletin=fecha_pub,
            programa_id=programa_id,
            inciso_id=str(fila.get("inciso_id", "")).strip() or None,
            principal_id=str(fila.get("principal_id", "")).strip() or None,
            aumento=aumento,
            reduccion=reduccion,
            monto_neto=monto_neto,
        )
        db.add(mod)
        insertadas += 1
        total_reduccion += reduccion
        total_aumento += aumento

    # Actualizar totales en la norma
    norma_obj.monto_total_reduccion = round(total_reduccion, 2)
    norma_obj.monto_total_ampliacion = round(total_aumento, 2)

    db.commit()
    print(
        f"  ✅ {norma_data['norma_id']}: "
        f"{insertadas} partidas | "
        f"-${total_reduccion:,.0f} | +${total_aumento:,.0f}"
    )
    return insertadas


async def sincronizar(desde: str = "01/01/2023"):
    """Pipeline completo de scraping + persistencia."""
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    print(f"\n{'='*60}")
    print(f"🚀 Sincronización MAP — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # 1. Scraper BORA
    scraper = BoraScraper()
    normas = await scraper.buscar_normas(desde=desde)
    print(f"\n📋 {len(normas)} normas detectadas. Procesando anexos...\n")

    total_mods = 0
    for norma in normas:
        try:
            n = await _procesar_norma(norma, db)
            total_mods += n
        except Exception as e:
            print(f"  ❌ Error procesando {norma.get('norma_id')}: {e}")
            db.rollback()

    # 2. Refrescar caché macro (fuerza re-descarga del BCRA)
    cargar_macro_indices.cache_clear()
    print("\n🔄 Caché de índices macro limpiada (se actualizará en próxima consulta)")

    db.close()
    print(f"\n{'='*60}")
    print(f"✅ Sincronización completa: {total_mods} modificaciones insertadas")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(sincronizar())
