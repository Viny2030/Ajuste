"""
analisis.py
───────────
Análisis del pipeline MAP sobre sql_app.db.

Queries disponibles:
  1. Recortes y ampliaciones por jurisdicción
  2. Evolución mensual de modificaciones
  3. Presupuesto original vs modificado por jurisdicción
  4. Presupuesto 2026 por programa (JGM)
  5. Recortes JGM cruzados por entidad — dónde cae el ajuste dentro de JGM
  6. Evolución mensual en pesos constantes (base dic-2023) y USD al TC del día de la DA

Nota de unidades:
  - presupuesto_base 2023: pesos nominales
  - presupuesto_base 2024/2025/2026: millones de pesos → se normalizan × 1.000.000
  - modificaciones: siempre en pesos nominales

Uso:
  python analisis.py                        # imprime todo en consola
  python analisis.py --exportar             # además guarda CSVs en data/analisis/
  python analisis.py --query 1              # solo la query 1
  python analisis.py --query 4 --exportar
"""

import argparse
import os
from pathlib import Path
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///sql_app.db")
engine = create_engine(DATABASE_URL)

EXPORT_DIR = Path("data/analisis")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt(val: float) -> str:
    """Formatea pesos nominales en B (billones), MM (miles de millones) o M (millones)."""
    if val is None:
        return "       n/d"
    av = abs(val)
    if av >= 1_000_000_000_000:
        return f"${val/1_000_000_000_000:>7.1f} B"
    if av >= 1_000_000_000:
        return f"${val/1_000_000_000:>7.1f} MM"
    if av >= 1_000_000:
        return f"${val/1_000_000:>7.1f} M"
    return f"${val:>10.0f}"


def _exportar(df: pd.DataFrame, nombre: str) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{nombre}_{datetime.today().strftime('%Y%m%d')}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  → Exportado: {path}")


# ── Query 1: Recortes y ampliaciones por jurisdicción ────────────────────────

def q1_recortes_por_jurisdiccion(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT
            m.jurisdiccion_id,
            MAX(p.jurisdiccion_desc)      AS jurisdiccion,
            COUNT(DISTINCT m.norma_id)    AS cant_normas,
            COUNT(1)                      AS cant_partidas,
            ROUND(SUM(m.reduccion), 2)    AS total_reduccion,
            ROUND(SUM(m.aumento), 2)      AS total_aumento,
            ROUND(SUM(m.monto_neto), 2)   AS neto
        FROM modificaciones m
        LEFT JOIN presupuesto_base p
               ON p.jurisdiccion_id = m.jurisdiccion_id
        WHERE m.jurisdiccion_id IS NOT NULL
        GROUP BY m.jurisdiccion_id
        ORDER BY total_reduccion DESC
    """
    df = pd.read_sql(text(sql), engine)

    print("\n" + "="*82)
    print("QUERY 1 — Recortes y ampliaciones por jurisdicción (pesos nominales)")
    print("="*82)
    print(f"{'Jur':>4}  {'Jurisdicción':<36}  {'DAs':>4}  {'Reducción':>12}  {'Aumento':>12}  {'Neto':>12}")
    print("-"*82)
    for _, r in df.iterrows():
        jur  = str(r.jurisdiccion_id or "")
        desc = str(r.jurisdiccion or "Sin descripción")[:36]
        print(
            f"{jur:>4}  {desc:<36}  {int(r.cant_normas):>4}  "
            f"{_fmt(r.total_reduccion):>12}  "
            f"{_fmt(r.total_aumento):>12}  "
            f"{_fmt(r.neto):>12}"
        )
    print("-"*82)
    totales = df[["total_reduccion", "total_aumento", "neto"]].sum()
    print(
        f"{'TOT':>4}  {'':36}  {'':>4}  "
        f"{_fmt(totales.total_reduccion):>12}  "
        f"{_fmt(totales.total_aumento):>12}  "
        f"{_fmt(totales.neto):>12}"
    )
    print("="*82)

    if exportar:
        _exportar(df, "q1_recortes_jurisdiccion")
    return df


# ── Query 2: Evolución mensual de modificaciones ─────────────────────────────

def q2_evolucion_mensual(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT
            STRFTIME('%Y-%m', m.fecha_boletin)  AS mes,
            COUNT(DISTINCT m.norma_id)           AS cant_normas,
            COUNT(1)                             AS cant_partidas,
            ROUND(SUM(m.reduccion), 2)           AS total_reduccion,
            ROUND(SUM(m.aumento), 2)             AS total_aumento,
            ROUND(SUM(m.monto_neto), 2)          AS neto_mensual
        FROM modificaciones m
        WHERE m.fecha_boletin IS NOT NULL
        GROUP BY mes
        ORDER BY mes
    """
    df = pd.read_sql(text(sql), engine)

    df["reduccion_acum"] = df["total_reduccion"].cumsum()
    df["aumento_acum"]   = df["total_aumento"].cumsum()
    df["neto_acum"]      = df["neto_mensual"].cumsum()

    print("\n" + "="*92)
    print("QUERY 2 — Evolución mensual de modificaciones presupuestarias (pesos nominales)")
    print("="*92)
    print(f"{'Mes':>7}  {'DAs':>4}  {'Reducción':>14}  {'Aumento':>14}  {'Neto mes':>14}  {'Neto acum':>14}")
    print("-"*92)
    for _, r in df.iterrows():
        print(
            f"{r.mes:>7}  {int(r.cant_normas):>4}  "
            f"{_fmt(r.total_reduccion):>14}  "
            f"{_fmt(r.total_aumento):>14}  "
            f"{_fmt(r.neto_mensual):>14}  "
            f"{_fmt(r.neto_acum):>14}"
        )
    print("-"*92)
    print(
        f"{'TOTAL':>7}  {int(df.cant_normas.sum()):>4}  "
        f"{_fmt(df.total_reduccion.sum()):>14}  "
        f"{_fmt(df.total_aumento.sum()):>14}  "
        f"{_fmt(df.neto_mensual.sum()):>14}"
    )
    print("="*92)

    if exportar:
        _exportar(df, "q2_evolucion_mensual")
    return df


# ── Query 3: Presupuesto original vs modificado por jurisdicción ─────────────

def q3_original_vs_modificado(exportar: bool = False) -> pd.DataFrame:
    # Normalización: 2024, 2025 y 2026 están en millones → × 1.000.000
    sql = """
        SELECT
            pb.jurisdiccion_id,
            MAX(pb.jurisdiccion_desc)   AS jurisdiccion,
            pb.ejercicio,
            ROUND(SUM(
                CASE
                    WHEN pb.ejercicio IN (2024, 2025, 2026)
                    THEN pb.monto_original * 1000000
                    ELSE pb.monto_original
                END
            ), 2) AS presupuesto_original,
            ROUND(SUM(
                CASE
                    WHEN pb.ejercicio IN (2024, 2025, 2026)
                    THEN pb.monto_vigente * 1000000
                    ELSE pb.monto_vigente
                END
            ), 2) AS presupuesto_vigente,
            ROUND(SUM(COALESCE(mods.total_reduccion, 0)), 2) AS reduccion_da,
            ROUND(SUM(COALESCE(mods.total_aumento,   0)), 2) AS aumento_da
        FROM presupuesto_base pb
        LEFT JOIN (
            SELECT
                jurisdiccion_id,
                ROUND(SUM(reduccion), 2) AS total_reduccion,
                ROUND(SUM(aumento),   2) AS total_aumento
            FROM modificaciones
            WHERE jurisdiccion_id IS NOT NULL
            GROUP BY jurisdiccion_id
        ) mods ON mods.jurisdiccion_id = pb.jurisdiccion_id
        GROUP BY pb.jurisdiccion_id, pb.ejercicio
        ORDER BY pb.ejercicio, reduccion_da DESC
    """
    df = pd.read_sql(text(sql), engine)

    df["var_pct"] = (
        (df["aumento_da"] - df["reduccion_da"])
        / df["presupuesto_original"].replace(0, float("nan"))
        * 100
    ).round(1)

    df["estimado_modificado"] = (
        df["presupuesto_original"] + df["aumento_da"] - df["reduccion_da"]
    )

    print("\n" + "="*97)
    print("QUERY 3 — Presupuesto original vs modificado por jurisdicción")
    print("          (presupuesto_base normalizado a pesos nominales)")
    print("="*97)

    for ejercicio, grupo in df.groupby("ejercicio"):
        grupo_vis = grupo[
            (grupo["presupuesto_original"] > 0) |
            (grupo["reduccion_da"] > 0) |
            (grupo["aumento_da"] > 0)
        ]
        print(f"\n  Ejercicio {ejercicio}")
        print(f"  {'Jur':>4}  {'Jurisdicción':<34}  {'Original':>13}  {'Reducción DA':>13}  {'Aumento DA':>13}  {'Var%':>7}")
        print("  " + "-"*92)
        for _, r in grupo_vis.iterrows():
            jur  = str(r.jurisdiccion_id or "")
            desc = str(r.jurisdiccion or "Sin descripción")[:34]
            var  = f"{r.var_pct:+.1f}%" if pd.notna(r.var_pct) else "    n/d"
            print(
                f"  {jur:>4}  {desc:<34}  "
                f"{_fmt(r.presupuesto_original):>13}  "
                f"{_fmt(r.reduccion_da):>13}  "
                f"{_fmt(r.aumento_da):>13}  "
                f"{var:>7}"
            )
        tot_orig = grupo_vis["presupuesto_original"].sum()
        tot_red  = grupo_vis["reduccion_da"].sum()
        tot_aum  = grupo_vis["aumento_da"].sum()
        tot_var  = (tot_aum - tot_red) / tot_orig * 100 if tot_orig else 0
        print("  " + "-"*92)
        print(
            f"  {'TOT':>4}  {'':34}  "
            f"{_fmt(tot_orig):>13}  "
            f"{_fmt(tot_red):>13}  "
            f"{_fmt(tot_aum):>13}  "
            f"{tot_var:+.1f}%"
        )

    print("\n" + "="*97)
    print("  Nota: reducción/aumento DA no está desagregada por ejercicio en la DB —")
    print("  el mismo monto aparece en los tres ejercicios. Ver query 2 para el total real.")
    print("="*97)

    if exportar:
        _exportar(df, "q3_original_vs_modificado")
    return df


# ── Query 4: Ejecución 2026 por programa (JGM) ───────────────────────────────

def q4_ejecucion_2026_jgm(exportar: bool = False) -> pd.DataFrame:
    """
    Muestra presupuestado vs vigente vs devengado por programa JGM en 2026.
    Los montos en presupuesto_base están en millones → se muestran en millones.
    """
    sql = """
        SELECT
            pb.programa_id,
            MAX(pb.programa_desc)            AS programa,
            MAX(pb.inciso_desc)              AS inciso_principal,
            ROUND(SUM(pb.monto_original), 2) AS presupuestado_mm,
            ROUND(SUM(pb.monto_vigente),  2) AS vigente_mm,
            ROUND(SUM(pb.monto_vigente) - SUM(pb.monto_original), 2) AS variacion_mm
        FROM presupuesto_base pb
        WHERE pb.ejercicio = 2026
          AND pb.jurisdiccion_id = '25'
        GROUP BY pb.programa_id
        ORDER BY presupuestado_mm DESC
    """
    df = pd.read_sql(text(sql), engine)

    if df.empty:
        print("\n[Q4] No hay datos de 2026 en la DB. Corré primero: python load_2026_to_db.py")
        return df

    df["var_pct"] = (
        df["variacion_mm"] / df["presupuestado_mm"].replace(0, float("nan")) * 100
    ).round(1)

    total_pres = df["presupuestado_mm"].sum()
    total_vig  = df["vigente_mm"].sum()
    total_var  = total_vig - total_pres

    print("\n" + "="*90)
    print("QUERY 4 — Presupuesto 2026 JGM por programa (millones ARS)")
    print("          Jurisdicción 25 incluye: JGM + CONICET, CONAE, Parques Nacionales,")
    print("          ENACOM, Turismo, Ambiente, AABE (absorbidos en reorganización 2024)")
    print("="*90)
    print(f"  {'ID':>3}  {'Programa':<46}  {'Presupuestado':>13}  {'Vigente':>13}  {'Var%':>7}")
    print("  " + "-"*84)
    for _, r in df.iterrows():
        var = f"{r.var_pct:+.1f}%" if pd.notna(r.var_pct) else "    n/d"
        print(
            f"  {r.programa_id:>3}  {str(r.programa)[:46]:<46}  "
            f"{r.presupuestado_mm:>13,.1f}  "
            f"{r.vigente_mm:>13,.1f}  "
            f"{var:>7}"
        )
    print("  " + "-"*84)
    var_pct_tot = (total_var / total_pres * 100) if total_pres else 0
    print(
        f"  {'TOT':>3}  {'':46}  "
        f"{total_pres:>13,.1f}  "
        f"{total_vig:>13,.1f}  "
        f"{var_pct_tot:+.1f}%"
    )
    print("="*90)
    print(f"  Unidad: millones ARS  |  Variación = vigente - presupuestado original")
    print(f"  Total presupuestado JGM 2026: {total_pres/1e6:,.2f} billones ARS")
    print("="*90)

    if exportar:
        _exportar(df, "q4_ejecucion_2026_jgm")
    return df


# ── Query 5: Recortes JGM por entidad ────────────────────────────────────────

def q5_recortes_jgm_por_entidad(exportar: bool = False) -> pd.DataFrame:
    """
    Cruza las modificaciones de JGM (jurisdiccion_id=25) contra presupuesto_base
    via partida_id para obtener la entidad afectada.
    """
    sql = """
        SELECT COALESCE(pb.entidad_id, '?')             AS entidad_id,
               COALESCE(pb.entidad_desc, 'Sin partida') AS entidad,
               COUNT(DISTINCT m.norma_id)               AS cant_normas,
               COUNT(1)                                 AS cant_partidas,
               ROUND(SUM(m.reduccion), 2)               AS total_reduccion,
               ROUND(SUM(m.aumento), 2)                 AS total_aumento,
               ROUND(SUM(m.monto_neto), 2)              AS neto
        FROM modificaciones m
        LEFT JOIN presupuesto_base pb ON pb.id = m.partida_id
        WHERE m.jurisdiccion_id = '25'
          AND (pb.ejercicio = 2026 OR m.partida_id IS NULL)
        GROUP BY COALESCE(pb.entidad_id, '?'), COALESCE(pb.entidad_desc, 'Sin partida')
        ORDER BY total_reduccion DESC
    """
    df_mods = pd.read_sql(text(sql), engine)

    sql_base = """
        SELECT
            entidad_id,
            MAX(entidad_desc)                       AS entidad_desc,
            ROUND(SUM(monto_original) * 1000000, 2) AS presup_original_2026
        FROM presupuesto_base
        WHERE ejercicio = 2026 AND jurisdiccion_id = '25'
        GROUP BY entidad_id
    """
    df_base = pd.read_sql(text(sql_base), engine)

    df = df_mods.merge(df_base, on="entidad_id", how="left")
    df["presup_original_2026"] = df["presup_original_2026"].fillna(0)

    df["recorte_pct_2026"] = (
        (df["total_reduccion"] - df["total_aumento"])
        / df["presup_original_2026"].replace(0, float("nan"))
        * 100
    ).round(1)

    print("\n" + "="*100)
    print("QUERY 5 — Recortes y ampliaciones JGM por entidad (pesos nominales)")
    print("          % calculado sobre presupuesto original 2026 de cada entidad")
    print("="*100)
    print(
        f"  {'Entidad':<48}  {'DAs':>4}  "
        f"{'Reducción':>13}  {'Aumento':>13}  {'Neto':>13}  {'% s/2026':>9}"
    )
    print("  " + "-"*94)

    for _, r in df.iterrows():
        entidad = str(r.entidad)[:48]
        pct = f"{r.recorte_pct_2026:+.1f}%" if pd.notna(r.recorte_pct_2026) else "      n/d"
        print(
            f"  {entidad:<48}  {int(r.cant_normas):>4}  "
            f"{_fmt(r.total_reduccion):>13}  "
            f"{_fmt(r.total_aumento):>13}  "
            f"{_fmt(r.neto):>13}  "
            f"{pct:>9}"
        )

    print("  " + "-"*94)
    tot_red  = df["total_reduccion"].sum()
    tot_aum  = df["total_aumento"].sum()
    tot_net  = df["neto"].sum()
    tot_base = df["presup_original_2026"].sum()
    tot_pct  = (tot_red - tot_aum) / tot_base * 100 if tot_base else 0
    print(
        f"  {'TOTAL JGM':<48}  {'':>4}  "
        f"{_fmt(tot_red):>13}  "
        f"{_fmt(tot_aum):>13}  "
        f"{_fmt(tot_net):>13}  "
        f"{tot_pct:+.1f}%"
    )
    print("="*100)
    print("  Nota: 'Sin partida' = modificaciones sin FK a presupuesto_base (partida_id NULL)")
    print("="*100)

    if exportar:
        _exportar(df, "q5_recortes_jgm_entidad")
    return df


# ── Query 6: Evolución mensual en pesos constantes y USD ─────────────────────

def q6_evolucion_real(exportar: bool = False) -> pd.DataFrame:
    """
    Reexpresa cada modificación mensual en:
      - Pesos constantes de dic-2023 (deflactados por IPC acumulado)
      - USD al TC oficial venta del día de la DA

    Criterio: TC e IPC al momento de cada Decisión Administrativa.
    Fuente: argentinadatos.com (tabla macro_indices).
    """
    import sqlite3
    from datetime import datetime, timedelta

    conn = sqlite3.connect("sql_app.db")
    cur = conn.cursor()

    # ── TC diario ─────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT fecha, valor FROM macro_indices
        WHERE indicador = 'TC_oficial_venta'
        ORDER BY fecha
    """)
    tc_diario = {r[0]: r[1] for r in cur.fetchall()}

    # ── IPC mensual → índice de nivel acumulado, base dic-2023 = 100 ─────────
    cur.execute("""
        SELECT fecha, valor FROM macro_indices
        WHERE indicador = 'IPC_variacion_mensual'
        ORDER BY fecha
    """)
    ipc_rows  = cur.fetchall()
    ipc_dict  = {r[0]: r[1] for r in ipc_rows}
    fechas_ipc = sorted(ipc_dict.keys())

    nivel = {}
    base  = 100.0
    for f in fechas_ipc:
        base = base * (1 + ipc_dict[f] / 100)
        nivel[f] = base

    dic23 = [f for f in fechas_ipc if f.startswith('2023-12')]
    base_dic23 = nivel[dic23[-1]] if dic23 else 1.0
    deflactor  = {f: nivel[f] / base_dic23 for f in nivel}

    conn.close()

    def get_tc(fecha_str: str):
        """TC del día exacto o el día hábil anterior disponible (hasta 10 días atrás)."""
        d = str(fecha_str)[:10]
        for i in range(10):
            c = (datetime.strptime(d, '%Y-%m-%d') - timedelta(days=i)).strftime('%Y-%m-%d')
            if c in tc_diario:
                return tc_diario[c]
        return None

    def get_deflactor(fecha_str: str):
        """Deflactor del mes de la DA. Si no hay, usa el más cercano anterior."""
        mes = str(fecha_str)[:7]
        candidatas = [f for f in deflactor if f.startswith(mes)]
        if candidatas:
            return deflactor[candidatas[-1]]
        anteriores = [f for f in sorted(deflactor.keys()) if f[:7] <= mes]
        return deflactor[anteriores[-1]] if anteriores else 1.0

    # ── Modificaciones agrupadas por mes ──────────────────────────────────────
    sql = """
        SELECT
            STRFTIME('%Y-%m', m.fecha_boletin) AS mes,
            m.fecha_boletin,
            ROUND(SUM(m.reduccion), 2)          AS reduccion,
            ROUND(SUM(m.aumento), 2)            AS aumento,
            ROUND(SUM(m.monto_neto), 2)         AS neto
        FROM modificaciones m
        WHERE m.fecha_boletin IS NOT NULL
        GROUP BY mes
        ORDER BY mes
    """
    df = pd.read_sql(text(sql), engine)

    # ── Aplicar TC y deflactor ────────────────────────────────────────────────
    registros = []
    for _, r in df.iterrows():
        fecha_ref = r['fecha_boletin']
        tc   = get_tc(str(fecha_ref))
        defl = get_deflactor(str(fecha_ref))
        red, aum, neto = r['reduccion'], r['aumento'], r['neto']
        registros.append({
            'mes':           r['mes'],
            'reduccion_nom': red,
            'aumento_nom':   aum,
            'neto_nom':      neto,
            'tc':            tc,
            'deflactor':     defl,
            'reduccion_cte': red  / defl if defl else None,
            'aumento_cte':   aum  / defl if defl else None,
            'neto_cte':      neto / defl if defl else None,
            'reduccion_usd': red  / tc   if tc   else None,
            'aumento_usd':   aum  / tc   if tc   else None,
            'neto_usd':      neto / tc   if tc   else None,
        })

    df6 = pd.DataFrame(registros)
    df6['neto_cte_acum'] = df6['neto_cte'].cumsum()
    df6['neto_usd_acum'] = df6['neto_usd'].cumsum()

    def _usd(val):
        if val is None or pd.isna(val):
            return "         n/d"
        av = abs(val)
        if av >= 1_000_000_000:
            return f"USD{val/1_000_000_000:>7.1f} B"
        if av >= 1_000_000:
            return f"USD{val/1_000_000:>7.1f} MM"
        return f"USD{val/1_000:>7.1f} K"

    print("\n" + "="*114)
    print("QUERY 6 — Evolución mensual en pesos constantes (base dic-2023) y USD")
    print("          TC e IPC al momento de cada Decisión Administrativa")
    print("="*114)
    print(
        f"  {'Mes':>7}  {'TC':>6}  {'Defl':>5}  "
        f"{'Neto nominal':>14}  {'Neto $ cte':>14}  {'Acum $ cte':>14}  "
        f"{'Neto USD':>12}  {'Acum USD':>12}"
    )
    print("  " + "-"*108)

    for _, r in df6.iterrows():
        tc_str   = f"{r['tc']:>6.0f}"       if r['tc']        else "   n/d"
        defl_str = f"{r['deflactor']:>5.2f}" if r['deflactor'] else "  n/d"
        print(
            f"  {r['mes']:>7}  {tc_str}  {defl_str}  "
            f"{_fmt(r['neto_nom']):>14}  "
            f"{_fmt(r['neto_cte']):>14}  "
            f"{_fmt(r['neto_cte_acum']):>14}  "
            f"{_usd(r['neto_usd']):>12}  "
            f"{_usd(r['neto_usd_acum']):>12}"
        )

    print("  " + "-"*108)
    tot_nom = df6['neto_nom'].sum()
    tot_cte = df6['neto_cte'].sum()
    tot_usd = df6['neto_usd'].sum()
    print(
        f"  {'TOTAL':>7}  {'':>6}  {'':>5}  "
        f"{_fmt(tot_nom):>14}  "
        f"{_fmt(tot_cte):>14}  "
        f"{'':>14}  "
        f"{_usd(tot_usd):>12}"
    )
    print("="*114)
    print("  Pesos constantes: base dic-2023 = 100  |  IPC fuente: argentinadatos.com")
    print("  USD: TC oficial venta al día de la DA   |  TC fuente: argentinadatos.com")
    print("="*114)

    if exportar:
        _exportar(df6, "q6_evolucion_real")
    return df6


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Análisis presupuestario MAP")
    p.add_argument(
        "--query", type=int, choices=[1, 2, 3, 4, 5, 6],
        help="Correr solo la query indicada (1-6). Sin este flag corre las seis."
    )
    p.add_argument(
        "--exportar", action="store_true",
        help=f"Exportar resultados como CSV en {EXPORT_DIR}/"
    )
    args = p.parse_args()

    queries = [args.query] if args.query else [1, 2, 3, 4, 5, 6]

    if 1 in queries:
        q1_recortes_por_jurisdiccion(exportar=args.exportar)
    if 2 in queries:
        q2_evolucion_mensual(exportar=args.exportar)
    if 3 in queries:
        q3_original_vs_modificado(exportar=args.exportar)
    if 4 in queries:
        q4_ejecucion_2026_jgm(exportar=args.exportar)
    if 5 in queries:
        q5_recortes_jgm_por_entidad(exportar=args.exportar)
    if 6 in queries:
        q6_evolucion_real(exportar=args.exportar)