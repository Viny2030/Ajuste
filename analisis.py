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
  7. Ajuste real por ministerio y programas especiales (pesos constantes y USD)

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


def _usd(val):
    if val is None or pd.isna(val):
        return "        n/d"
    av = abs(val)
    if av >= 1e9:  return f"USD{val/1e9:>7.1f} B"
    if av >= 1e6:  return f"USD{val/1e6:>7.1f} MM"
    return               f"USD{val/1e3:>7.1f} K"


def _exportar(df: pd.DataFrame, nombre: str) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{nombre}_{datetime.today().strftime('%Y%m%d')}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  → Exportado: {path}")


def _cargar_macro():
    """Carga TC diario y deflactor IPC (base dic-2023) desde macro_indices."""
    import sqlite3
    conn = sqlite3.connect("sql_app.db")
    cur  = conn.cursor()

    cur.execute("SELECT fecha, valor FROM macro_indices WHERE indicador='TC_oficial_venta' ORDER BY fecha")
    tc_diario = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("SELECT fecha, valor FROM macro_indices WHERE indicador='IPC_variacion_mensual' ORDER BY fecha")
    ipc_rows   = cur.fetchall()
    ipc_dict   = {r[0]: r[1] for r in ipc_rows}
    fechas_ipc = sorted(ipc_dict.keys())

    nivel = {}
    base  = 100.0
    for f in fechas_ipc:
        base = base * (1 + ipc_dict[f] / 100)
        nivel[f] = base

    dic23      = [f for f in fechas_ipc if f.startswith('2023-12')]
    base_dic23 = nivel[dic23[-1]] if dic23 else 1.0
    deflactor  = {f: nivel[f] / base_dic23 for f in nivel}
    conn.close()
    return tc_diario, deflactor


def _get_tc(tc_diario, fecha_str):
    from datetime import datetime, timedelta
    d = str(fecha_str)[:10]
    for i in range(10):
        c = (datetime.strptime(d, '%Y-%m-%d') - timedelta(days=i)).strftime('%Y-%m-%d')
        if c in tc_diario:
            return tc_diario[c]
    return None


def _get_deflactor(deflactor, fecha_str):
    mes   = str(fecha_str)[:7]
    cands = [f for f in deflactor if f.startswith(mes)]
    if cands:
        return deflactor[cands[-1]]
    prev = [f for f in sorted(deflactor) if f[:7] <= mes]
    return deflactor[prev[-1]] if prev else 1.0


# ── Query 1 ───────────────────────────────────────────────────────────────────

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
        LEFT JOIN presupuesto_base p ON p.jurisdiccion_id = m.jurisdiccion_id
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
        print(
            f"{str(r.jurisdiccion_id or ''):>4}  {str(r.jurisdiccion or 'Sin descripción')[:36]:<36}  "
            f"{int(r.cant_normas):>4}  "
            f"{_fmt(r.total_reduccion):>12}  {_fmt(r.total_aumento):>12}  {_fmt(r.neto):>12}"
        )
    print("-"*82)
    tot = df[["total_reduccion","total_aumento","neto"]].sum()
    print(f"{'TOT':>4}  {'':36}  {'':>4}  {_fmt(tot.total_reduccion):>12}  {_fmt(tot.total_aumento):>12}  {_fmt(tot.neto):>12}")
    print("="*82)

    if exportar: _exportar(df, "q1_recortes_jurisdiccion")
    return df


# ── Query 2 ───────────────────────────────────────────────────────────────────

def q2_evolucion_mensual(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT
            STRFTIME('%Y-%m', m.fecha_boletin) AS mes,
            COUNT(DISTINCT m.norma_id)          AS cant_normas,
            COUNT(1)                            AS cant_partidas,
            ROUND(SUM(m.reduccion), 2)          AS total_reduccion,
            ROUND(SUM(m.aumento), 2)            AS total_aumento,
            ROUND(SUM(m.monto_neto), 2)         AS neto_mensual
        FROM modificaciones m
        WHERE m.fecha_boletin IS NOT NULL
        GROUP BY mes ORDER BY mes
    """
    df = pd.read_sql(text(sql), engine)
    df["neto_acum"] = df["neto_mensual"].cumsum()

    print("\n" + "="*92)
    print("QUERY 2 — Evolución mensual de modificaciones presupuestarias (pesos nominales)")
    print("="*92)
    print(f"{'Mes':>7}  {'DAs':>4}  {'Reducción':>14}  {'Aumento':>14}  {'Neto mes':>14}  {'Neto acum':>14}")
    print("-"*92)
    for _, r in df.iterrows():
        print(
            f"{r.mes:>7}  {int(r.cant_normas):>4}  "
            f"{_fmt(r.total_reduccion):>14}  {_fmt(r.total_aumento):>14}  "
            f"{_fmt(r.neto_mensual):>14}  {_fmt(r.neto_acum):>14}"
        )
    print("-"*92)
    print(
        f"{'TOTAL':>7}  {int(df.cant_normas.sum()):>4}  "
        f"{_fmt(df.total_reduccion.sum()):>14}  {_fmt(df.total_aumento.sum()):>14}  "
        f"{_fmt(df.neto_mensual.sum()):>14}"
    )
    print("="*92)

    if exportar: _exportar(df, "q2_evolucion_mensual")
    return df


# ── Query 3 ───────────────────────────────────────────────────────────────────

def q3_original_vs_modificado(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT
            pb.jurisdiccion_id, MAX(pb.jurisdiccion_desc) AS jurisdiccion, pb.ejercicio,
            ROUND(SUM(CASE WHEN pb.ejercicio IN (2024,2025,2026) THEN pb.monto_original*1000000 ELSE pb.monto_original END),2) AS presupuesto_original,
            ROUND(SUM(CASE WHEN pb.ejercicio IN (2024,2025,2026) THEN pb.monto_vigente*1000000  ELSE pb.monto_vigente  END),2) AS presupuesto_vigente,
            ROUND(SUM(COALESCE(mods.total_reduccion,0)),2) AS reduccion_da,
            ROUND(SUM(COALESCE(mods.total_aumento,  0)),2) AS aumento_da
        FROM presupuesto_base pb
        LEFT JOIN (
            SELECT jurisdiccion_id,
                   ROUND(SUM(reduccion),2) AS total_reduccion,
                   ROUND(SUM(aumento),  2) AS total_aumento
            FROM modificaciones WHERE jurisdiccion_id IS NOT NULL
            GROUP BY jurisdiccion_id
        ) mods ON mods.jurisdiccion_id = pb.jurisdiccion_id
        GROUP BY pb.jurisdiccion_id, pb.ejercicio
        ORDER BY pb.ejercicio, reduccion_da DESC
    """
    df = pd.read_sql(text(sql), engine)
    df["var_pct"] = ((df["aumento_da"]-df["reduccion_da"]) / df["presupuesto_original"].replace(0,float("nan"))*100).round(1)

    print("\n" + "="*97)
    print("QUERY 3 — Presupuesto original vs modificado por jurisdicción")
    print("          (presupuesto_base normalizado a pesos nominales)")
    print("="*97)

    for ejercicio, grupo in df.groupby("ejercicio"):
        gv = grupo[(grupo["presupuesto_original"]>0)|(grupo["reduccion_da"]>0)|(grupo["aumento_da"]>0)]
        print(f"\n  Ejercicio {ejercicio}")
        print(f"  {'Jur':>4}  {'Jurisdicción':<34}  {'Original':>13}  {'Reducción DA':>13}  {'Aumento DA':>13}  {'Var%':>7}")
        print("  "+"-"*92)
        for _, r in gv.iterrows():
            var = f"{r.var_pct:+.1f}%" if pd.notna(r.var_pct) else "    n/d"
            print(f"  {str(r.jurisdiccion_id or ''):>4}  {str(r.jurisdiccion or '')[:34]:<34}  "
                  f"{_fmt(r.presupuesto_original):>13}  {_fmt(r.reduccion_da):>13}  {_fmt(r.aumento_da):>13}  {var:>7}")
        tot_o = gv["presupuesto_original"].sum()
        tot_r = gv["reduccion_da"].sum()
        tot_a = gv["aumento_da"].sum()
        print("  "+"-"*92)
        print(f"  {'TOT':>4}  {'':34}  {_fmt(tot_o):>13}  {_fmt(tot_r):>13}  {_fmt(tot_a):>13}  {(tot_a-tot_r)/tot_o*100 if tot_o else 0:+.1f}%")

    print("\n"+"="*97)
    print("  Nota: reducción/aumento DA no está desagregada por ejercicio — ver query 2 para el total real.")
    print("="*97)

    if exportar: _exportar(df, "q3_original_vs_modificado")
    return df


# ── Query 4 ───────────────────────────────────────────────────────────────────

def q4_ejecucion_2026_jgm(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT pb.programa_id, MAX(pb.programa_desc) AS programa, MAX(pb.inciso_desc) AS inciso_principal,
               ROUND(SUM(pb.monto_original),2) AS presupuestado_mm,
               ROUND(SUM(pb.monto_vigente), 2) AS vigente_mm,
               ROUND(SUM(pb.monto_vigente)-SUM(pb.monto_original),2) AS variacion_mm
        FROM presupuesto_base pb
        WHERE pb.ejercicio=2026 AND pb.jurisdiccion_id='25'
        GROUP BY pb.programa_id ORDER BY presupuestado_mm DESC
    """
    df = pd.read_sql(text(sql), engine)
    if df.empty:
        print("\n[Q4] Sin datos 2026. Corré primero: python load_2026_to_db.py")
        return df

    df["var_pct"] = (df["variacion_mm"]/df["presupuestado_mm"].replace(0,float("nan"))*100).round(1)
    tp, tv = df["presupuestado_mm"].sum(), df["vigente_mm"].sum()

    print("\n"+"="*90)
    print("QUERY 4 — Presupuesto 2026 JGM por programa (millones ARS)")
    print("="*90)
    print(f"  {'ID':>3}  {'Programa':<46}  {'Presupuestado':>13}  {'Vigente':>13}  {'Var%':>7}")
    print("  "+"-"*84)
    for _, r in df.iterrows():
        var = f"{r.var_pct:+.1f}%" if pd.notna(r.var_pct) else "    n/d"
        print(f"  {r.programa_id:>3}  {str(r.programa)[:46]:<46}  {r.presupuestado_mm:>13,.1f}  {r.vigente_mm:>13,.1f}  {var:>7}")
    print("  "+"-"*84)
    print(f"  {'TOT':>3}  {'':46}  {tp:>13,.1f}  {tv:>13,.1f}  {(tv-tp)/tp*100 if tp else 0:+.1f}%")
    print("="*90)
    print(f"  Total presupuestado JGM 2026: {tp/1e6:,.2f} billones ARS")
    print("="*90)

    if exportar: _exportar(df, "q4_ejecucion_2026_jgm")
    return df


# ── Query 5 ───────────────────────────────────────────────────────────────────

def q5_recortes_jgm_por_entidad(exportar: bool = False) -> pd.DataFrame:
    sql = """
        SELECT COALESCE(pb.entidad_id,'?') AS entidad_id,
               COALESCE(pb.entidad_desc,'Sin partida') AS entidad,
               COUNT(DISTINCT m.norma_id) AS cant_normas, COUNT(1) AS cant_partidas,
               ROUND(SUM(m.reduccion),2) AS total_reduccion,
               ROUND(SUM(m.aumento),  2) AS total_aumento,
               ROUND(SUM(m.monto_neto),2) AS neto
        FROM modificaciones m
        LEFT JOIN presupuesto_base pb ON pb.id=m.partida_id
        WHERE m.jurisdiccion_id='25' AND (pb.ejercicio=2026 OR m.partida_id IS NULL)
        GROUP BY COALESCE(pb.entidad_id,'?'), COALESCE(pb.entidad_desc,'Sin partida')
        ORDER BY total_reduccion DESC
    """
    df_mods = pd.read_sql(text(sql), engine)

    sql_base = """
        SELECT entidad_id, MAX(entidad_desc) AS entidad_desc,
               ROUND(SUM(monto_original)*1000000,2) AS presup_original_2026
        FROM presupuesto_base WHERE ejercicio=2026 AND jurisdiccion_id='25'
        GROUP BY entidad_id
    """
    df_base = pd.read_sql(text(sql_base), engine)
    df = df_mods.merge(df_base, on="entidad_id", how="left")
    df["presup_original_2026"] = df["presup_original_2026"].fillna(0)
    df["recorte_pct_2026"] = ((df["total_reduccion"]-df["total_aumento"])/df["presup_original_2026"].replace(0,float("nan"))*100).round(1)

    print("\n"+"="*100)
    print("QUERY 5 — Recortes y ampliaciones JGM por entidad (pesos nominales)")
    print("          % calculado sobre presupuesto original 2026 de cada entidad")
    print("="*100)
    print(f"  {'Entidad':<48}  {'DAs':>4}  {'Reducción':>13}  {'Aumento':>13}  {'Neto':>13}  {'% s/2026':>9}")
    print("  "+"-"*94)
    for _, r in df.iterrows():
        pct = f"{r.recorte_pct_2026:+.1f}%" if pd.notna(r.recorte_pct_2026) else "      n/d"
        print(f"  {str(r.entidad)[:48]:<48}  {int(r.cant_normas):>4}  "
              f"{_fmt(r.total_reduccion):>13}  {_fmt(r.total_aumento):>13}  {_fmt(r.neto):>13}  {pct:>9}")
    print("  "+"-"*94)
    tr,ta,tn,tb = df["total_reduccion"].sum(),df["total_aumento"].sum(),df["neto"].sum(),df["presup_original_2026"].sum()
    print(f"  {'TOTAL JGM':<48}  {'':>4}  {_fmt(tr):>13}  {_fmt(ta):>13}  {_fmt(tn):>13}  {(tr-ta)/tb*100 if tb else 0:+.1f}%")
    print("="*100)
    print("  Nota: 'Sin partida' = modificaciones sin FK a presupuesto_base (partida_id NULL)")
    print("="*100)

    if exportar: _exportar(df, "q5_recortes_jgm_entidad")
    return df


# ── Query 6 ───────────────────────────────────────────────────────────────────

def q6_evolucion_real(exportar: bool = False) -> pd.DataFrame:
    tc_diario, deflactor = _cargar_macro()

    sql = """
        SELECT STRFTIME('%Y-%m', m.fecha_boletin) AS mes, m.fecha_boletin,
               ROUND(SUM(m.reduccion),2) AS reduccion,
               ROUND(SUM(m.aumento),  2) AS aumento,
               ROUND(SUM(m.monto_neto),2) AS neto
        FROM modificaciones m WHERE m.fecha_boletin IS NOT NULL
        GROUP BY mes ORDER BY mes
    """
    df = pd.read_sql(text(sql), engine)

    registros = []
    for _, r in df.iterrows():
        tc   = _get_tc(tc_diario, str(r['fecha_boletin']))
        defl = _get_deflactor(deflactor, str(r['fecha_boletin']))
        red, aum, neto = r['reduccion'], r['aumento'], r['neto']
        registros.append({
            'mes': r['mes'], 'tc': tc, 'deflactor': defl,
            'reduccion_nom': red, 'aumento_nom': aum, 'neto_nom': neto,
            'reduccion_cte': red/defl  if defl else None,
            'aumento_cte':   aum/defl  if defl else None,
            'neto_cte':      neto/defl if defl else None,
            'reduccion_usd': red/tc    if tc   else None,
            'aumento_usd':   aum/tc    if tc   else None,
            'neto_usd':      neto/tc   if tc   else None,
        })

    df6 = pd.DataFrame(registros)
    df6['neto_cte_acum'] = df6['neto_cte'].cumsum()
    df6['neto_usd_acum'] = df6['neto_usd'].cumsum()

    print("\n"+"="*114)
    print("QUERY 6 — Evolución mensual en pesos constantes (base dic-2023) y USD")
    print("          TC e IPC al momento de cada Decisión Administrativa")
    print("="*114)
    print(f"  {'Mes':>7}  {'TC':>6}  {'Defl':>5}  {'Neto nominal':>14}  {'Neto $ cte':>14}  {'Acum $ cte':>14}  {'Neto USD':>12}  {'Acum USD':>12}")
    print("  "+"-"*108)
    for _, r in df6.iterrows():
        tc_s   = f"{r['tc']:>6.0f}"       if r['tc']        else "   n/d"
        defl_s = f"{r['deflactor']:>5.2f}" if r['deflactor'] else "  n/d"
        print(f"  {r['mes']:>7}  {tc_s}  {defl_s}  "
              f"{_fmt(r['neto_nom']):>14}  {_fmt(r['neto_cte']):>14}  {_fmt(r['neto_cte_acum']):>14}  "
              f"{_usd(r['neto_usd']):>12}  {_usd(r['neto_usd_acum']):>12}")
    print("  "+"-"*108)
    print(f"  {'TOTAL':>7}  {'':>6}  {'':>5}  {_fmt(df6['neto_nom'].sum()):>14}  {_fmt(df6['neto_cte'].sum()):>14}  {'':>14}  {_usd(df6['neto_usd'].sum()):>12}")
    print("="*114)
    print("  Pesos constantes: base dic-2023 = 100  |  IPC fuente: argentinadatos.com")
    print("  USD: TC oficial venta al día de la DA   |  TC fuente: argentinadatos.com")
    print("="*114)

    if exportar: _exportar(df6, "q6_evolucion_real")
    return df6


# ── Query 7 ───────────────────────────────────────────────────────────────────

def q7_ajuste_real(exportar: bool = False) -> pd.DataFrame:
    """
    Ajuste real por ministerio y programas especiales.
    Sección 1: resumen por ministerio en nominal, pesos constantes y USD.
    Sección 2: top 5 programas por ministerio.
    Sección 3: tablas especiales (sueldos, jubilaciones, niñez, salud, SIDE, obras).
    """
    tc_diario, deflactor = _cargar_macro()

    print("\n"+"="*110)
    print("QUERY 7 — Ajuste real por ministerio y programas especiales")
    print("          Pesos constantes base dic-2023  |  USD al TC del día de la DA")
    print("="*110)

    # ── 1. Por ministerio ─────────────────────────────────────────────────────
    sql_min = """
        SELECT m.jurisdiccion_id,
               MAX(m.fecha_boletin) AS fecha_boletin,
               COALESCE(MAX(pb.jurisdiccion_desc), m.jurisdiccion_id) AS jur_desc,
               ROUND(SUM(m.reduccion),  2) AS reduccion,
               ROUND(SUM(m.aumento),    2) AS aumento,
               ROUND(SUM(m.monto_neto), 2) AS neto
        FROM modificaciones m
        LEFT JOIN presupuesto_base pb ON pb.jurisdiccion_id = m.jurisdiccion_id
        WHERE m.jurisdiccion_id IS NOT NULL AND m.fecha_boletin IS NOT NULL
        GROUP BY m.jurisdiccion_id
        ORDER BY SUM(m.monto_neto) DESC
    """
    df_min = pd.read_sql(text(sql_min), engine)

    print(f"\n{'─'*110}")
    print("  1. RESUMEN POR MINISTERIO")
    print(f"  {'Jur':<5}  {'Descripción':<42}  {'Neto nominal':>14}  {'Neto $ cte':>14}  {'Neto USD':>13}")
    print(f"  {'─'*100}")

    tot_nom = tot_cte = tot_usd = 0.0
    for _, r in df_min.iterrows():
        tc   = _get_tc(tc_diario, str(r['fecha_boletin']))   or 1
        defl = _get_deflactor(deflactor, str(r['fecha_boletin'])) or 1
        neto_cte = r['neto'] / defl
        neto_usd = r['neto'] / tc
        tot_nom += r['neto']; tot_cte += neto_cte; tot_usd += neto_usd
        print(f"  {str(r['jurisdiccion_id']):<5}  {str(r['jur_desc'])[:42]:<42}  "
              f"{_fmt(r['neto']):>14}  {_fmt(neto_cte):>14}  {_usd(neto_usd):>13}")
    print(f"  {'─'*100}")
    print(f"  {'TOT':<5}  {'':42}  {_fmt(tot_nom):>14}  {_fmt(tot_cte):>14}  {_usd(tot_usd):>13}")

    # ── 2. Top 5 programas por ministerio ─────────────────────────────────────
    sql_prog = """
        SELECT m.jurisdiccion_id,
               COALESCE(MAX(pb2.jurisdiccion_desc), m.jurisdiccion_id) AS jur_desc,
               COALESCE(pb.programa_id, m.programa_id)                 AS programa_id,
               COALESCE(MAX(pb.programa_desc), m.programa_id)          AS prog_desc,
               MAX(m.fecha_boletin)       AS fecha_boletin,
               ROUND(SUM(m.reduccion),  2) AS reduccion,
               ROUND(SUM(m.aumento),    2) AS aumento,
               ROUND(SUM(m.monto_neto), 2) AS neto
        FROM modificaciones m
        LEFT JOIN presupuesto_base pb  ON pb.id = m.partida_id
        LEFT JOIN presupuesto_base pb2 ON pb2.jurisdiccion_id = m.jurisdiccion_id
        WHERE m.fecha_boletin IS NOT NULL
        GROUP BY m.jurisdiccion_id, COALESCE(pb.programa_id, m.programa_id)
        ORDER BY m.jurisdiccion_id, SUM(m.monto_neto) DESC
    """
    df_prog = pd.read_sql(text(sql_prog), engine)

    print(f"\n{'─'*110}")
    print("  2. TOP 5 PROGRAMAS POR MINISTERIO")
    print(f"  {'Jur':<5}  {'Prog':<5}  {'Descripción':<40}  {'Neto nominal':>14}  {'Neto $ cte':>14}  {'Neto USD':>13}")
    print(f"  {'─'*104}")

    for jur_id, grupo in df_prog.groupby('jurisdiccion_id'):
        print(f"\n  ── {str(grupo['jur_desc'].iloc[0])[:70]}")
        for _, r in grupo.head(5).iterrows():
            tc   = _get_tc(tc_diario, str(r['fecha_boletin']))   or 1
            defl = _get_deflactor(deflactor, str(r['fecha_boletin'])) or 1
            print(f"  {str(jur_id):<5}  {str(r['programa_id']):<5}  {str(r['prog_desc'])[:40]:<40}  "
                  f"{_fmt(r['neto']):>14}  {_fmt(r['neto']/defl):>14}  {_usd(r['neto']/tc):>13}")

    # ── 3. Tablas especiales ──────────────────────────────────────────────────
    print(f"\n\n{'='*110}")
    print("  3. TABLAS ESPECIALES")
    print(f"{'='*110}")

    especiales = [
        ("SUELDOS ESTATALES — Inciso 1 (Gastos en personal), todos los ministerios",
         "pb.inciso_id = '1'"),
        ("JUBILACIONES Y PREVISIÓN SOCIAL — jur=88, prog 16/17/21/30/31/99",
         "m.jurisdiccion_id = '88' AND m.programa_id IN ('16','17','21','30','31','99')"),
        ("NIÑEZ Y JUVENTUD — jur=85+88, programas identificados",
         "m.jurisdiccion_id IN ('85','88') AND m.programa_id IN ('33','32','44','45','47','52','20')"),
        ("SALUD — jur=80 (Ministerio de Salud completo)",
         "m.jurisdiccion_id = '80'"),
        ("SIDE / INTELIGENCIA DE ESTADO — jur=20",
         "m.jurisdiccion_id = '20' AND pb.entidad_desc LIKE '%Inteligencia de Estado%'"),
        ("OBRAS PÚBLICAS — jur=64 + inciso 4 (Bienes de uso) todos los ministerios",
         "m.jurisdiccion_id = '64' OR pb.inciso_id = '4'"),
    ]

    for titulo, where in especiales:
        sql_esp = f"""
            SELECT MAX(m.fecha_boletin)        AS fecha_boletin,
                   ROUND(SUM(m.reduccion),  2) AS reduccion,
                   ROUND(SUM(m.aumento),    2) AS aumento,
                   ROUND(SUM(m.monto_neto), 2) AS neto
            FROM modificaciones m
            LEFT JOIN presupuesto_base pb ON pb.id = m.partida_id
            WHERE m.fecha_boletin IS NOT NULL AND ({where})
        """
        df_esp = pd.read_sql(text(sql_esp), engine)

        if df_esp.empty or df_esp['neto'].isna().all() or df_esp['fecha_boletin'].isna().all():
            print(f"\n  ── {titulo}: sin datos")
            continue

        r    = df_esp.iloc[0]
        tc   = _get_tc(tc_diario, str(r['fecha_boletin']))   or 1
        defl = _get_deflactor(deflactor, str(r['fecha_boletin'])) or 1

        print(f"\n  {'─'*108}")
        print(f"  {titulo}")
        print(f"  {'':22}  {'Reducción':>14}  {'Aumento':>14}  {'Neto':>14}")
        print(f"  {'─'*68}")
        print(f"  {'Nominal':<22}  {_fmt(r['reduccion']):>14}  {_fmt(r['aumento']):>14}  {_fmt(r['neto']):>14}")
        print(f"  {'$ constantes dic-23':<22}  {_fmt(r['reduccion']/defl):>14}  {_fmt(r['aumento']/defl):>14}  {_fmt(r['neto']/defl):>14}")
        print(f"  {'USD (TC día DA)':<22}  {_usd(r['reduccion']/tc):>14}  {_usd(r['aumento']/tc):>14}  {_usd(r['neto']/tc):>14}")

    print("\n"+"="*110)
    print("  Pesos constantes: base dic-2023 = 100  |  IPC fuente: argentinadatos.com")
    print("  USD: TC oficial venta al día de la DA   |  TC fuente: argentinadatos.com")
    print("="*110)

    if exportar: _exportar(df_min, "q7_ajuste_real_ministerio")
    return df_min


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Análisis presupuestario MAP")
    p.add_argument("--query", type=int, choices=[1,2,3,4,5,6,7],
                   help="Correr solo la query indicada (1-7). Sin este flag corre las siete.")
    p.add_argument("--exportar", action="store_true",
                   help=f"Exportar resultados como CSV en {EXPORT_DIR}/")
    args = p.parse_args()

    queries = [args.query] if args.query else [1,2,3,4,5,6,7]

    if 1 in queries: q1_recortes_por_jurisdiccion(exportar=args.exportar)
    if 2 in queries: q2_evolucion_mensual(exportar=args.exportar)
    if 3 in queries: q3_original_vs_modificado(exportar=args.exportar)
    if 4 in queries: q4_ejecucion_2026_jgm(exportar=args.exportar)
    if 5 in queries: q5_recortes_jgm_por_entidad(exportar=args.exportar)
    if 6 in queries: q6_evolucion_real(exportar=args.exportar)
    if 7 in queries: q7_ajuste_real(exportar=args.exportar)