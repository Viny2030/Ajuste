"""
scripts/seed_presupuesto_base.py

Carga los archivos credito-anual-YYYY.csv en la tabla presupuesto_base.
Agrupa por partida única (jurisdiccion + entidad + programa + inciso + principal +
parcial + subparcial + fuente + ubicacion) sumando montos — porque el CSV tiene
una fila por unidad ejecutora/actividad/obra y el modelo guarda a nivel partida.

Uso:
    python scripts/seed_presupuesto_base.py
    python scripts/seed_presupuesto_base.py --años 2024 2025
    python scripts/seed_presupuesto_base.py --limpiar   # borra todo antes de insertar
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# Columnas clave para agrupar (identidad de la partida)
CLAVE = [
    "ejercicio_presupuestario",
    "jurisdiccion_id", "jurisdiccion_desc",
    "entidad_id",      "entidad_desc",
    "programa_id",     "programa_desc",
    "subprograma_id",
    "proyecto_id",
    "actividad_id",
    "obra_id",
    "inciso_id",       "inciso_desc",
    "principal_id",    "principal_desc",
    "parcial_id",      "parcial_desc",
    "subparcial_id",   "subparcial_desc",
    "fuente_financiamiento_id", "fuente_financiamiento_desc",
    "ubicacion_geografica_id",
]

MONTOS = ["credito_presupuestado", "credito_vigente"]


def _float(s: str) -> float:
    """Parsea float con coma o punto decimal, maneja vacíos."""
    if not s or s.strip() == "":
        return 0.0
    return float(s.strip().replace(".", "").replace(",", "."))


def cargar_csv(path: Path) -> dict[tuple, dict]:
    """Lee el CSV y agrupa partidas sumando montos."""
    partidas: dict[tuple, dict] = {}

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        filas = 0
        for row in reader:
            filas += 1
            clave = tuple(row.get(c, "0").strip() for c in CLAVE)

            if clave not in partidas:
                partidas[clave] = {c: row.get(c, "").strip() for c in CLAVE}
                for m in MONTOS:
                    partidas[clave][m] = 0.0

            for m in MONTOS:
                partidas[clave][m] += _float(row.get(m, "0"))

    logger.info("%s: %d filas → %d partidas únicas", path.name, filas, len(partidas))
    return partidas


def seed(años: list[int], limpiar: bool = False) -> None:
    from app.database.session import SessionLocal, engine
    from app.database import models

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        if limpiar:
            eliminadas = db.query(models.PresupuestoBase).delete()
            db.commit()
            logger.info("Tabla limpiada: %d registros eliminados", eliminadas)

        for año in años:
            # Buscar archivo
            candidatos = [
                Path(f"data/credito-anual-{año}.csv"),
                Path(f"data/presupuesto_{año}.csv"),
            ]
            csv_path = next((p for p in candidatos if p.exists()), None)
            if not csv_path:
                logger.warning("No se encontró CSV para ejercicio %s (buscado: %s)", año, candidatos)
                continue

            # Saltar si ya hay datos para este ejercicio
            existentes = db.query(models.PresupuestoBase).filter_by(ejercicio=año).count()
            if existentes > 0 and not limpiar:
                logger.info("Ejercicio %s ya tiene %d partidas — saltando (usá --limpiar para reemplazar)", año, existentes)
                continue

            partidas = cargar_csv(csv_path)
            insertadas = 0

            for clave, datos in partidas.items():
                obj = models.PresupuestoBase(
                    ejercicio             = int(datos["ejercicio_presupuestario"]),
                    jurisdiccion_id       = datos["jurisdiccion_id"],
                    jurisdiccion_desc     = datos["jurisdiccion_desc"],
                    entidad_id            = datos["entidad_id"],
                    entidad_desc          = datos["entidad_desc"],
                    programa_id           = datos["programa_id"],
                    programa_desc         = datos["programa_desc"],
                    subprograma_id        = datos["subprograma_id"] or None,
                    proyecto_id           = datos["proyecto_id"] or None,
                    actividad_id          = datos["actividad_id"] or None,
                    obra_id               = datos["obra_id"] or None,
                    inciso_id             = datos["inciso_id"],
                    inciso_desc           = datos["inciso_desc"],
                    principal_id          = datos["principal_id"],
                    principal_desc        = datos["principal_desc"],
                    parcial_id            = datos["parcial_id"] or None,
                    parcial_desc          = datos["parcial_desc"] or None,
                    subparcial_id         = datos["subparcial_id"] or None,
                    subparcial_desc       = datos["subparcial_desc"] or None,
                    fuente_financiamiento_id   = datos["fuente_financiamiento_id"],
                    fuente_financiamiento_desc = datos["fuente_financiamiento_desc"],
                    ubicacion_geografica_id    = datos["ubicacion_geografica_id"] or None,
                    monto_original        = datos["credito_presupuestado"],
                    monto_vigente         = datos["credito_vigente"],
                )
                db.add(obj)
                insertadas += 1

                if insertadas % 10000 == 0:
                    db.flush()
                    logger.info("  %s: %d partidas insertadas...", año, insertadas)

            db.commit()
            logger.info("✅ Ejercicio %s: %d partidas insertadas", año, insertadas)

    except Exception as e:
        db.rollback()
        logger.error("Error: %s", e)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Seed presupuesto_base desde CSVs de crédito anual")
    p.add_argument("--años", nargs="+", type=int, default=[2023, 2024, 2025],
                   help="Años a cargar (default: 2023 2024 2025)")
    p.add_argument("--limpiar", action="store_true",
                   help="Borrar todos los registros existentes antes de insertar")
    args = p.parse_args()

    seed(años=args.años, limpiar=args.limpiar)