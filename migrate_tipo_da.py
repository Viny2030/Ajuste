"""
migrate_tipo_da.py
──────────────────
Migración one-shot:
1. Agrega la columna tipo_da a normas_jgm (si no existe)
2. Clasifica las DAs conocidas sin tabla de gasto
3. Marca como GASTO las que sí tienen modificaciones
4. Deja DESCONOCIDO las nuevas que entren en el futuro

Uso:
    python migrate_tipo_da.py
    python migrate_tipo_da.py --dry-run   # muestra cambios sin aplicar
"""

import argparse
import os
from sqlalchemy import create_engine, text, func
from sqlalchemy.orm import Session

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///sql_app.db")
engine = create_engine(DATABASE_URL)

# ── Clasificación manual de las 17 DAs sin tabla ─────────────────────────────
# Revisá los títulos y ajustá si alguna es DEUDA o RECURSOS en vez de NORMATIVA.
# Títulos obtenidos del check_sin_tabla.py:
#   DA-1-2026    | PRESUPUESTO
#   DA-1015-2024 | MODIFICACIONES
#   DA-1018-2024 | MODIFICACION
#   DA-1022-2024 | MODIFICACION
#   DA-18-2026   | PRESUPUESTO
#   DA-2-2026    | PRESUPUESTO
#   DA-23-2024   | PRESUPUESTO
#   DA-24-2025   | MODIFICACION
#   DA-3-2025    | PRESUPUESTO
#   DA-301351-2023 | PRESUPUESTO
#   DA-39-2025   | MODIFICACION
#   DA-4-2026    | ADMINISTRACIÓN PÚBLICA NACIONAL
#   DA-5-2024    | PRESUPUESTO
#   DA-861-2024  | MODIFICACION
#   DA-88-2023   | PRESUPUESTO
#   DA-9-2026    | ADMINISTRACIÓN PÚBLICA NACIONAL
#   DA-910-2024  | MODIFICACION
#
# Por ahora todas van como NORMATIVA — significa "no reintentar el PDF".
# Podés cambiar a DEUDA o RECURSOS las que correspondan una vez que revises
# los PDFs manualmente.

SIN_TABLA: dict[str, str] = {
    "DA-1-2026":      "NORMATIVA",
    "DA-1015-2024":   "NORMATIVA",
    "DA-1018-2024":   "NORMATIVA",
    "DA-1022-2024":   "NORMATIVA",
    "DA-18-2026":     "NORMATIVA",
    "DA-2-2026":      "NORMATIVA",
    "DA-23-2024":     "NORMATIVA",
    "DA-24-2025":     "NORMATIVA",
    "DA-3-2025":      "NORMATIVA",
    "DA-301351-2023": "NORMATIVA",
    "DA-39-2025":     "NORMATIVA",
    "DA-4-2026":      "NORMATIVA",
    "DA-5-2024":      "NORMATIVA",
    "DA-861-2024":    "NORMATIVA",
    "DA-88-2023":     "NORMATIVA",
    "DA-9-2026":      "NORMATIVA",
    "DA-910-2024":    "NORMATIVA",
}


def _columna_existe(conn) -> bool:
    """Verifica si tipo_da ya existe en normas_jgm (SQLite)."""
    result = conn.execute(text("PRAGMA table_info(normas_jgm)"))
    columnas = [row[1] for row in result]
    return "tipo_da" in columnas


def migrar(dry_run: bool = False) -> None:
    with engine.connect() as conn:
        # ── 1. Agregar columna si no existe ──────────────────────────────────
        if _columna_existe(conn):
            print("✓ Columna tipo_da ya existe, salteando ALTER TABLE")
        else:
            sql = "ALTER TABLE normas_jgm ADD COLUMN tipo_da VARCHAR(20) DEFAULT 'DESCONOCIDO'"
            print(f"  ALTER TABLE: {sql}")
            if not dry_run:
                conn.execute(text(sql))
                conn.commit()
                print("✓ Columna tipo_da agregada")

        # ── 2. Marcar DAs sin tabla como NORMATIVA ────────────────────────────
        print(f"\nClasificando {len(SIN_TABLA)} DAs sin tabla...")
        for norma_id, tipo in SIN_TABLA.items():
            print(f"  {norma_id:20} → {tipo}")
            if not dry_run:
                conn.execute(
                    text("UPDATE normas_jgm SET tipo_da = :tipo WHERE norma_id = :nid"),
                    {"tipo": tipo, "nid": norma_id},
                )
        if not dry_run:
            conn.commit()
            print("✓ DAs sin tabla marcadas")

        # ── 3. Marcar como GASTO las que sí tienen modificaciones ─────────────
        print("\nMarcando DAs con modificaciones como GASTO...")
        sql_con_mods = """
            UPDATE normas_jgm
            SET tipo_da = 'GASTO'
            WHERE (tipo_da IS NULL OR tipo_da = 'DESCONOCIDO')
              AND norma_id IN (
                  SELECT DISTINCT norma_id FROM modificaciones
              )
        """
        if not dry_run:
            result = conn.execute(text(sql_con_mods))
            conn.commit()
            print(f"✓ {result.rowcount} DAs marcadas como GASTO")
        else:
            # Contar cuántas serían afectadas
            result = conn.execute(text("""
                SELECT COUNT(DISTINCT n.norma_id)
                FROM normas_jgm n
                JOIN modificaciones m ON m.norma_id = n.norma_id
                WHERE n.tipo_da IS NULL OR n.tipo_da = 'DESCONOCIDO'
            """))
            count = result.scalar()
            print(f"  (dry-run) Se marcarían {count} DAs como GASTO")

        # ── 4. Resumen final ──────────────────────────────────────────────────
        print("\n── Resumen post-migración ──────────────────────────────────────")
        if not dry_run:
            rows = conn.execute(text("""
                SELECT tipo_da, COUNT(*) as cant
                FROM normas_jgm
                GROUP BY tipo_da
                ORDER BY cant DESC
            """))
            for row in rows:
                print(f"  {str(row[0]):15} → {row[1]} DAs")
        print("────────────────────────────────────────────────────────────────")
        if dry_run:
            print("  (dry-run: ningún cambio fue aplicado)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Mostrar cambios sin aplicar")
    args = p.parse_args()
    migrar(dry_run=args.dry_run)