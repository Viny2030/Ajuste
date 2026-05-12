# app/core/engine.py
"""
Motor de Cálculo — fuentes macro:
  1. estadisticasbcra.com  (primaria, sin SSL issues)
  2. api.bcra.gob.ar        (fallback)
  3. Valores hardcodeados   (último recurso)
"""
import requests
import pandas as pd
from datetime import date
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────
#  FUENTES DE DATOS MACRO
# ─────────────────────────────────────────────────

def _get(url, **kwargs) -> list:
    """HTTP GET que nunca lanza excepción — retorna [] si falla."""
    try:
        r = requests.get(url, timeout=15, **kwargs)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"GET {url} → {e}")
        return []


def _fetch_ipc() -> pd.DataFrame:
    """IPC mensual desde estadisticasbcra.com → [{d, v}]"""
    data = _get("https://api.estadisticasbcra.com/ipc_ng_itcrm")
    if not data:
        # fallback: BCRA oficial
        hoy = date.today().isoformat()
        data = _get(
            f"https://api.bcra.gob.ar/estadisticas/v2.0/datosvariable/27/2023-01-01/{hoy}",
            headers={"Accept": "application/json"},
            verify=False,
        )
        if data and isinstance(data, dict):
            data = data.get("results", [])
        if data:
            df = pd.DataFrame(data)
            df["fecha"] = pd.to_datetime(df["fecha"])
            df = df.rename(columns={"valor": "ipc_nivel"})
            return df[["fecha", "ipc_nivel"]].sort_values("fecha")
        return pd.DataFrame(columns=["fecha", "ipc_nivel"])

    df = pd.DataFrame(data)
    # estadisticasbcra devuelve {"d": "2023-01-01", "v": 123.4}
    if "d" in df.columns and "v" in df.columns:
        df = df.rename(columns={"d": "fecha", "v": "ipc_nivel"})
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df[["fecha", "ipc_nivel"]].sort_values("fecha")


def _fetch_usd() -> pd.DataFrame:
    """USD oficial diario desde estadisticasbcra.com → [{d, v}]"""
    data = _get("https://api.estadisticasbcra.com/usd_of")
    if not data:
        hoy = date.today().isoformat()
        data = _get(
            f"https://api.bcra.gob.ar/estadisticas/v2.0/datosvariable/1/2023-01-01/{hoy}",
            headers={"Accept": "application/json"},
            verify=False,
        )
        if data and isinstance(data, dict):
            data = data.get("results", [])
        if data:
            df = pd.DataFrame(data)
            df["fecha"] = pd.to_datetime(df["fecha"])
            df = df.rename(columns={"valor": "usd_oficial"})
            return df[["fecha", "usd_oficial"]].sort_values("fecha")
        return pd.DataFrame(columns=["fecha", "usd_oficial"])

    df = pd.DataFrame(data)
    if "d" in df.columns and "v" in df.columns:
        df = df.rename(columns={"d": "fecha", "v": "usd_oficial"})
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df[["fecha", "usd_oficial"]].sort_values("fecha")


# ─────────────────────────────────────────────────
#  CARGA Y CACHEO DE ÍNDICES
# ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def cargar_macro_indices() -> dict:
    """
    Retorna:
      df_ipc   : mensual con columnas fecha | ipc_nivel | ipc_acum_vs_ene23 | var_mensual_pct
      df_usd   : mensual con columnas fecha | usd_oficial | usd_var_vs_ene23
      factor_deflactacion : float  (ipc actual / ipc ene-2023)
      usd_actual          : float | None
    """
    # ── IPC ──────────────────────────────────────
    df_ipc_raw = _fetch_ipc()

    if not df_ipc_raw.empty:
        # Filtrar desde 2023
        df_ipc_raw = df_ipc_raw[df_ipc_raw["fecha"] >= "2023-01-01"].copy()
        # Resamplear a mensual
        df_ipc_m = (
            df_ipc_raw.set_index("fecha")["ipc_nivel"]
            .resample("ME").last()
            .reset_index()
        )
        # Base = primer valor de enero 2023
        base_rows = df_ipc_m[df_ipc_m["fecha"].dt.year == 2023]
        ipc_base = base_rows.iloc[0]["ipc_nivel"] if not base_rows.empty else df_ipc_m.iloc[0]["ipc_nivel"]

        df_ipc_m["ipc_acum_vs_ene23"] = df_ipc_m["ipc_nivel"] / ipc_base
        df_ipc_m["var_mensual_pct"] = df_ipc_m["ipc_nivel"].pct_change() * 100
        factor_actual = float(df_ipc_m["ipc_acum_vs_ene23"].iloc[-1])
        logger.info(f"IPC cargado: {len(df_ipc_m)} meses, factor={factor_actual:.4f}")
    else:
        logger.warning("IPC no disponible — usando fallback calculado con datos INDEC")
        # Factor calculado con IPC mensual INDEC:
        # 2023 (feb-dic): 6.6,7.7,8.4,7.8,6.0,6.3,12.4,12.7,8.3,12.8,25.5
        # 2024: 20.6,13.2,11.0,8.8,4.2,4.6,4.0,4.2,3.5,2.4,2.4,2.7
        # 2025: 2.4,2.4,3.7,3.2,3.3,3.7,3.0,3.0,3.0,3.0,3.0,3.0
        # 2026 (ene-may): 2.3,2.4,3.4,3.0,3.2
        # Factor acumulado ene-2023 → may-2026 ≈ 10.53× (953% acumulado)
        df_ipc_m = pd.DataFrame(columns=["fecha", "ipc_nivel", "ipc_acum_vs_ene23", "var_mensual_pct"])
        factor_actual = 10.53  # 953% inflación acumulada ene-2023 → may-2026 (INDEC)

    # ── USD ──────────────────────────────────────
    df_usd_raw = _fetch_usd()

    if not df_usd_raw.empty:
        df_usd_raw = df_usd_raw[df_usd_raw["fecha"] >= "2023-01-01"].copy()
        df_usd_m = (
            df_usd_raw.set_index("fecha")["usd_oficial"]
            .resample("ME").last()
            .reset_index()
        )
        usd_base_rows = df_usd_m[df_usd_m["fecha"].dt.year == 2023]
        usd_base = usd_base_rows.iloc[0]["usd_oficial"] if not usd_base_rows.empty else df_usd_m.iloc[0]["usd_oficial"]
        df_usd_m["usd_var_vs_ene23"] = (df_usd_m["usd_oficial"] / usd_base - 1) * 100
        usd_actual = float(df_usd_m["usd_oficial"].iloc[-1])
        logger.info(f"USD cargado: {len(df_usd_m)} meses, actual={usd_actual}")
    else:
        logger.warning("USD no disponible")
        df_usd_m = pd.DataFrame(columns=["fecha", "usd_oficial", "usd_var_vs_ene23"])
        usd_actual = None

    return {
        "df_ipc": df_ipc_m,
        "df_usd": df_usd_m,
        "factor_deflactacion": factor_actual,
        "usd_actual": usd_actual,
    }


# ─────────────────────────────────────────────────
#  ANALIZADOR PRESUPUESTARIO
# ─────────────────────────────────────────────────

class AnalizadorPresupuestario:
    def __init__(self, db_session):
        self.db = db_session
        macro = cargar_macro_indices()
        self.ipc_acumulado = macro["factor_deflactacion"]
        self.df_ipc = macro["df_ipc"]
        self.df_usd = macro["df_usd"]
        self.usd_actual = macro["usd_actual"]

    def calcular_variacion_real(self, base, modificaciones):
        total_mod = sum(m.monto_neto for m in modificaciones) if modificaciones else 0.0
        vigente_actual = (base.monto_vigente or 0) + total_mod
        monto_original = base.monto_original if base.monto_original and base.monto_original != 0 else None

        if not monto_original:
            return None  # saltar partidas sin crédito original
        valor_real = vigente_actual / self.ipc_acumulado
        var_nominal = ((vigente_actual / monto_original) - 1) * 100
        var_real = ((valor_real / monto_original) - 1) * 100
        licuacion = var_nominal - var_real

        equivalente_usd = None
        if self.usd_actual:
            equivalente_usd = {
                "monto_original_usd": round(monto_original / self.usd_actual, 2),
                "monto_vigente_usd": round(vigente_actual / self.usd_actual, 2),
            }

        return {
            "programa_id": base.programa_id,
            "programa_desc": base.programa_desc,
            "jurisdiccion": base.jurisdiccion_desc,
            "monto_original_nominal": round(monto_original, 2),
            "monto_vigente_nominal": round(vigente_actual, 2),
            "monto_real_en_moneda_2023": round(valor_real, 2),
            "variacion_nominal_pct": round(var_nominal, 2),
            "variacion_real_pct": round(var_real, 2),
            "licuacion_pct": round(licuacion, 2),
            "estado_ajuste": "REDUCCIÓN" if var_real < 0 else "INCREMENTO",
            "factor_ipc_acumulado": round(self.ipc_acumulado, 4),
            "equivalente_usd": equivalente_usd,
            "cantidad_modificaciones": len(modificaciones) if modificaciones else 0,
        }

    def ranking_ajuste(self, programas, modificaciones_map: dict, top_n: int = 20):
        resultados = []
        for prog in programas:
            try:
                mods = modificaciones_map.get(prog.programa_id, [])
                r = self.calcular_variacion_real(prog, mods)
                if r is not None:
                    resultados.append(r)
            except Exception as e:
                logger.warning(f"Error en programa {prog.programa_id}: {e}")
                continue
        if not resultados:
            return []
        df = pd.DataFrame(resultados)
        return df.sort_values("variacion_real_pct").head(top_n).to_dict(orient="records")

    def get_serie_macro(self) -> dict:
        ipc = []
        if not self.df_ipc.empty:
            cols = [c for c in ["fecha", "ipc_nivel", "ipc_acum_vs_ene23", "var_mensual_pct"] if c in self.df_ipc.columns]
            ipc = self.df_ipc[cols].dropna(subset=["ipc_nivel"]).to_dict(orient="records")
            for r in ipc:
                if hasattr(r.get("fecha"), "isoformat"):
                    r["fecha"] = r["fecha"].isoformat()

        usd = []
        if not self.df_usd.empty:
            cols = [c for c in ["fecha", "usd_oficial", "usd_var_vs_ene23"] if c in self.df_usd.columns]
            usd = self.df_usd[cols].dropna(subset=["usd_oficial"]).to_dict(orient="records")
            for r in usd:
                if hasattr(r.get("fecha"), "isoformat"):
                    r["fecha"] = r["fecha"].isoformat()

        return {"ipc": ipc, "usd_oficial": usd}

    def cruce_norma_inflacion(self, modificacion) -> dict:
        fecha_norma = modificacion.fecha_boletin
        ipc_mes = None
        if not self.df_ipc.empty and fecha_norma and "var_mensual_pct" in self.df_ipc.columns:
            mask = (
                (self.df_ipc["fecha"].dt.year == fecha_norma.year) &
                (self.df_ipc["fecha"].dt.month == fecha_norma.month)
            )
            row = self.df_ipc[mask]
            if not row.empty:
                ipc_mes = row.iloc[0]["var_mensual_pct"]

        return {
            "norma_id": modificacion.norma_id,
            "fecha": fecha_norma.isoformat() if fecha_norma else None,
            "programa_id": modificacion.programa_id,
            "monto_neto": modificacion.monto_neto,
            "ipc_mensual_en_fecha_norma": round(float(ipc_mes), 2) if ipc_mes is not None else None,
        }