# app/core/daily_sync.py
"""
Pipeline de sincronización diaria:
  1. Infoleg (CSV)   → descubrimiento masivo de DAs (actualización mensual)
  2. BORA API        → DAs recientes de los últimos días (tiempo real)
  3. Fusión          → dedup por norma_id, BORA prevalece en caso de conflicto
  4. PDF download    → intenta Infoleg primero, luego BORA
  5. pdf_processor   → extrae tabla de partidas del PDF
  6. DB              → persiste NormaJGM + ModificacionPresupuestaria
  7. Cache clear     → invalida caché macro (fuerza re-descarga del BCRA)

Uso:
  python -m app.core.daily_sync
  python -m app.core.daily_sync --desde 10/12/2023
  python -m app.core.daily_sync --solo-recientes   # solo BORA últimos 30 días
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database.session import SessionLocal, engine
from app.database import models
from app.scrapers.bora_discovery import buscar_normas, descargar_pdf_norma
from app.scrapers.bora_scraper import buscar_normas_bora, descargar_pdf_bora
from app.core.pdf_processor import extraer_tabla_presupuesto
from app.core.engine import cargar_macro_indices

logger = logging.getLogger(__name__)

RAW_PDF_DIR = Path("data/raw_pdfs")
RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)


# ── Fusión de fuentes ─────────────────────────────────────────────────────────

def _fusionar_normas(
    normas_infoleg: list[dict],
    normas_bora: list[dict],
) -> list[dict]:
    """
    Fusiona normas de Infoleg y BORA API en una lista deduplicada.
    Para la misma norma_id, BORA prevalece (tiene URL más directa al PDF).
    """
    fusionadas: dict[str, dict] = {}

    # Primero Infoleg (base)
    for n in normas_infoleg:
        fusionadas[n["norma_id"]] = n

    # Luego BORA (sobreescribe si ya existe, agrega campos extra como id_aviso_bora)
    for n in normas_bora:
        nid = n["norma_id"]
        if nid in fusionadas:
            # Enriquecer el registro de Infoleg con datos del BORA
            fusionadas[nid].update({
                "id_aviso_bora": n.get("id_aviso_bora"),
                "fecha_bora_str": n.get("fecha_bora_str"),
                "url_bora": n.get("url_bora") or fusionadas[nid].get("url_bora"),
            })
        else:
            fusionadas[nid] = n

    resultado = sorted(fusionadas.values(), key=lambda x: x["fecha_boletin"])
    return resultado


# ── Descarga de PDFs (multi-fuente) ──────────────────────────────────────────

async def _descargar_pdf(norma_data: dict, pdf_path: Path) -> Optional[str]:
    """
    Intenta descargar el PDF de la DA probando múltiples fuentes.
    Orden: Infoleg → BORA API → url_bora directo.
    Retorna la ruta local del PDF si tuvo éxito, None si no.
    """
    if pdf_path.exists() and pdf_path.stat().st_size > 500:
        return str(pdf_path)

    # Extraer fecha_boletin en formato YYYYMMDD (sin guiones) para el fallback BORA
    fecha_fmt = norma_data.get("fecha_boletin", "").replace("-", "")

    # Fuente 1: Infoleg (url_infoleg → busca .pdf o texact.pdf)
    url_infoleg = norma_data.get("url_infoleg", "")
    if url_infoleg:
        resultado = await descargar_pdf_norma(
            url_infoleg, str(pdf_path), fecha_boletin=fecha_fmt
        )
        if resultado:
            return resultado

    # Fuente 2: BORA API (usa id_aviso_bora para encontrar el PDF del Anexo)
    if norma_data.get("id_aviso_bora"):
        resultado = await descargar_pdf_bora(norma_data, str(pdf_path))
        if resultado:
            return resultado

    # Fuente 3: url_bora directa como última opción
    url_bora = norma_data.get("url_bora", "")
    if url_bora and url_bora != url_infoleg:
        resultado = await descargar_pdf_norma(
            url_bora, str(pdf_path), fecha_boletin=fecha_fmt
        )
        if resultado:
            return resultado

    return None


# ── Procesamiento de una norma ────────────────────────────────────────────────

async def _procesar_norma(norma_data: dict, db: Session) -> int:
    """
    Persiste una norma y sus modificaciones de partidas.
    Retorna la cantidad de filas de modificaciones insertadas.
    """
    norma_id = norma_data["norma_id"]

    # Dedup: si ya está en DB, saltar
    existente = (
        db.query(models.NormaJGM)
        .filter(models.NormaJGM.norma_id == norma_id)
        .first()
    )
    if existente:
        return 0

    # Parsear fecha
    fecha_str = norma_data.get("fecha_boletin", "")
    fecha_pub: Optional[datetime] = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            fecha_pub = datetime.strptime(fecha_str, fmt)
            break
        except ValueError:
            continue

    # Persistir NormaJGM
    norma_obj = models.NormaJGM(
        norma_id=norma_id,
        tipo_norma=norma_data.get("tipo_norma", "DA"),
        numero=str(norma_data.get("numero", "")),
        anio=int(norma_data.get("anio", datetime.now().year)),
        fecha_publicacion=fecha_pub,
        titulo=norma_data.get("titulo", ""),
        url_bora=norma_data.get("url_bora", ""),
        pdf_url=norma_data.get("url_infoleg", ""),
        tipo_accion=norma_data.get("tipo_accion"),
        pdf_hash=norma_data.get("pdf_hash"),
    )
    db.add(norma_obj)
    db.flush()

    # Descargar PDF
    safe_id = norma_id.replace("/", "_").replace(" ", "_")
    pdf_path = RAW_PDF_DIR / f"{safe_id}.pdf"
    downloaded = await _descargar_pdf(norma_data, pdf_path)

    if not downloaded:
        print(f"  ⚠️  Sin PDF para {norma_id} — norma guardada sin partidas")
        db.commit()
        return 0

    # Extraer tabla de partidas
    df_partidas = extraer_tabla_presupuesto(downloaded)
    if df_partidas is None or df_partidas.empty:
        print(f"  ⚠️  Tabla vacía o no extraíble: {norma_id}")
        db.commit()
        return 0

    # Persistir modificaciones
    insertadas = 0
    total_reduccion = 0.0
    total_aumento = 0.0

    for _, fila in df_partidas.iterrows():
        programa_id = str(fila.get("programa_id") or "").strip()
        if not programa_id:
            continue

        reduccion = float(fila.get("reduccion") or fila.get("disminucion") or 0)
        aumento   = float(fila.get("aumento") or 0)
        monto_neto = aumento - reduccion

        # Buscar partida_id en presupuesto_base si existe
        partida_id: Optional[int] = None
        jur_id = str(fila.get("jurisdiccion_id") or "").strip()
        inciso_id = str(fila.get("inciso_id") or "").strip() or None
        principal_id = str(fila.get("principal_id") or "").strip() or None

        if jur_id and programa_id:
            partida = (
                db.query(models.PresupuestoBase)
                .filter(
                    models.PresupuestoBase.jurisdiccion_id == jur_id,
                    models.PresupuestoBase.programa_id == programa_id,
                )
                .first()
            )
            if partida:
                partida_id = partida.id

        mod = models.ModificacionPresupuestaria(
            norma_db_id=norma_obj.id,
            norma_id=norma_id,
            fecha_boletin=fecha_pub,
            partida_id=partida_id,
            programa_id=programa_id,
            inciso_id=inciso_id,
            principal_id=principal_id,
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
        f"  ✅ {norma_id}: "
        f"{insertadas} partidas | "
        f"↓ ${total_reduccion:>15,.0f} | ↑ ${total_aumento:>15,.0f}"
    )
    return insertadas


# ── Pipeline principal ────────────────────────────────────────────────────────

async def sincronizar(
    desde: str = "10/12/2023",
    solo_recientes: bool = False,
) -> None:
    """
    Pipeline completo:
      - Infoleg CSV (histórico completo desde `desde`)
      - BORA API (últimos 30 días si solo_recientes=True, sino desde `desde`)
      - Fusión, dedup, persistencia
    """
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    print(f"\n{'='*60}")
    print(f"🚀 Sincronización MAP — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # ── 1. Infoleg (CSV completo, cache local) ────────────────────────────────
    normas_infoleg: list[dict] = []
    if not solo_recientes:
        print("📂 Fuente 1: Infoleg CSV...")
        try:
            normas_infoleg = buscar_normas(desde=desde)
            print(f"   → {len(normas_infoleg)} DAs presupuestarias encontradas en Infoleg")
        except Exception as e:
            print(f"   ⚠️  Infoleg falló: {e}")

    # ── 2. BORA API (recientes) ───────────────────────────────────────────────
    print("\n📡 Fuente 2: BORA API (tiempo real)...")
    desde_bora = (
        (date.today() - timedelta(days=30)).strftime("%d/%m/%Y")
        if solo_recientes
        else desde
    )
    normas_bora: list[dict] = []
    try:
        normas_bora = await buscar_normas_bora(desde=desde_bora)
        print(f"   → {len(normas_bora)} DAs presupuestarias encontradas en BORA")
    except Exception as e:
        print(f"   ⚠️  BORA API falló: {e}")

    # ── 3. Fusión ──────────────────────────────────────────────────────────────
    normas = _fusionar_normas(normas_infoleg, normas_bora)
    print(f"\n🔀 Total fusionado (dedup): {len(normas)} normas únicas\n")

    if not normas:
        print("ℹ️  Sin normas nuevas para procesar.")
        db.close()
        return

    # ── 4. Procesar norma por norma ────────────────────────────────────────────
    print(f"📋 Procesando PDFs y extrayendo partidas...\n")
    total_mods = 0
    errores = 0

    for i, norma in enumerate(normas, 1):
        try:
            n = await _procesar_norma(norma, db)
            total_mods += n
        except Exception as e:
            nid = norma.get("norma_id", "?")
            print(f"  ❌ Error en {nid}: {e}")
            logger.exception("Error procesando %s", nid)
            db.rollback()
            errores += 1

        # Pausa cada 10 normas para no saturar los servidores
        if i % 10 == 0:
            await asyncio.sleep(1)

    # ── 5. Invalidar caché macro ──────────────────────────────────────────────
    cargar_macro_indices.cache_clear()
    print("\n🔄 Caché de índices macro limpiada")

    db.close()
    print(f"\n{'='*60}")
    print(f"✅ Sincronización completa")
    print(f"   Normas procesadas: {len(normas)}")
    print(f"   Modificaciones insertadas: {total_mods}")
    print(f"   Errores: {errores}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    p = argparse.ArgumentParser(description="Sincronizador MAP — Infoleg + BORA")
    p.add_argument(
        "--desde",
        default="10/12/2023",
        help="Fecha inicio DD/MM/YYYY (default: inicio gestión Milei)",
    )
    p.add_argument(
        "--solo-recientes",
        action="store_true",
        help="Solo buscar en BORA últimos 30 días (más rápido, para runs diarios)",
    )
    args = p.parse_args()

    asyncio.run(sincronizar(desde=args.desde, solo_recientes=args.solo_recientes))