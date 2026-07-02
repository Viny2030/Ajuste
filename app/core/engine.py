# app/core/engine.py
"""
Motor de Cálculo — fuentes macro:
  1. api.bcra.gob.ar v4.0   (oficial, primaria — reemplaza a v2.0/v3.0, deprecadas)
  2. estadisticasbcra.com   (secundaria, best-effort — requiere token propio hoy)
  3. Valores hardcodeados   (último recurso — SIEMPRE loguea WARNING si se usa)

Nota (2026-07): las fuentes que usaba este módulo antes dejaron de funcionar:
  - api.bcra.gob.ar v2.0 → 410 Gone ("Método deprecado")
  - api.bcra.gob.ar v3.0 → 410 Gone ("Método deprecado")
  - api.estadisticasbcra.com → ipc_ng_itcrm da 404, usd_of pide token de acceso
La API vigente y gratuita, sin autenticación, es v4.0 ("Estadísticas Monetarias"):
  https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/{idVariable}?desde=...&hasta=...
IDs usados acá (confirmados contra el catálogo real, ver
  https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias):
  - id 5  = "Tipo de cambio mayorista de referencia" (diario) → usd_oficial
  - id 27 = "Inflación mensual" (% mensual, no es un nivel/índice) → se compone
            manualmente en un índice acumulado (ver _fetch_ipc)
⚠️ Los IDs de variable NO son estables entre versiones de la API — si esto
vuelve a romperse, no asumir que los números 5/27 siguen significando lo
mismo; volver a consultar el catálogo primero.
"""
import requests
import pandas as pd
from datetime import date
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

BCRA_V4_BASE = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
ID_USD_MAYORISTA = 5
ID_INFLACION_MENSUAL = 27

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


def _fetch_bcra_v4(id_variable: int, desde: str = "2023-01-01") -> list[dict]:
    """
    Consulta una serie de la API oficial BCRA v4.0. Devuelve lista de
    {"fecha": "YYYY-MM-DD", "valor": float} ordenada ascendente, o [] si falla.
    No requiere autenticación (API pública).
    """
    hoy = date.today().isoformat()
    url = f"{BCRA_V4_BASE}/{id_variable}?desde={desde}&hasta={hoy}"
    data = _get(url, headers={"Accept": "application/json"}, verify=True)
    if not data or not isinstance(data, dict):
        return []
    resultados = data.get("results", [])
    if not resultados:
        return []
    detalle = resultados[0].get("detalle", [])
    return sorted(detalle, key=lambda r: r["fecha"])


def _fetch_ipc() -> pd.DataFrame:
    """
    Índice de nivel de precios mensual, compuesto a partir de la Inflación
    mensual (%) oficial del BCRA v4.0 (la API no publica un nivel/índice
    directo, solo la variación % mes a mes).
    Base = 100 en el primer mes disponible desde 2023-01.
    """
    detalle = _fetch_bcra_v4(ID_INFLACION_MENSUAL)

    if not detalle:
        logger.warning("IPC no disponible desde BCRA v4.0 — intentando estadisticasbcra.com")
        data = _get("https://api.estadisticasbcra.com/ipc_ng_itcrm")
        if data:
            df = pd.DataFrame(data)
            if "d" in df.columns and "v" in df.columns:
                df = df.rename(columns={"d": "fecha", "v": "ipc_nivel"})
            df["fecha"] = pd.to_datetime(df["fecha"])
            return df[["fecha", "ipc_nivel"]].sort_values("fecha")
        return pd.DataFrame(columns=["fecha", "ipc_nivel"])

    nivel = 100.0
    filas = []
    for row in detalle:
        fecha = pd.to_datetime(row["fecha"])
        if fecha.strftime("%Y-%m") != "2023-01":
            # el mes base (2023-01) no aplica su propia inflación
            nivel *= (1 + row["valor"] / 100)
        filas.append({"fecha": fecha, "ipc_nivel": nivel})

    df = pd.DataFrame(filas)
    logger.info(f"IPC compuesto desde BCRA v4.0: {len(df)} meses, "
                f"nivel final={df['ipc_nivel'].iloc[-1]:.2f}")
    return df.sort_values("fecha")


def _fetch_usd() -> pd.DataFrame:
    """
    Tipo de cambio mayorista de referencia (diario), desde la API oficial
    BCRA v4.0.
    """
    detalle = _fetch_bcra_v4(ID_USD_MAYORISTA)

    if not detalle:
        logger.warning("USD no disponible desde BCRA v4.0 — intentando estadisticasbcra.com")
        data = _get("https://api.estadisticasbcra.com/usd_of")
        if data:
            df = pd.DataFrame(data)
            if "d" in df.columns and "v" in df.columns:
                df = df.rename(columns={"d": "fecha", "v": "usd_oficial"})
            df["fecha"] = pd.to_datetime(df["fecha"])
            return df[["fecha", "usd_oficial"]].sort_values("fecha")
        return pd.DataFrame(columns=["fecha", "usd_oficial"])

    df = pd.DataFrame(detalle).rename(columns={"valor": "usd_oficial"})
    df["fecha"] = pd.to_datetime(df["fecha"])
    logger.info(f"USD cargado desde BCRA v4.0: {len(df)} registros, "
                f"último={df['usd_oficial'].iloc[-1]:.2f} ({df['fecha'].iloc[-1].date()})")
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
        logger.error(
            "⚠️  IPC no disponible de NINGUNA fuente (BCRA v4.0 y estadisticasbcra.com "
            "fallaron). Usando factor hardcodeado — este valor NO se actualiza solo y "
            "va a quedar desactualizado con el tiempo. Revisar por qué fallaron las "
            "fuentes en vez de confiar en este número por mucho tiempo."
        )
        # Factor acumulado ene-2023 → may-2026, calculado con la inflación
        # mensual oficial del BCRA v4.0 (id_variable=27) el 2026-07-02.
        # Recalcular si esta rama llega a usarse: no hardcodear un valor nuevo
        # sin volver a consultar la fuente real primero.
        df_ipc_m = pd.DataFrame(columns=["fecha", "ipc_nivel", "ipc_acum_vs_ene23", "var_mensual_pct"])
        factor_actual = 9.6368  # 863.68% acumulado ene-2023 → may-2026 (BCRA v4.0, id 27)

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

        # Precalcular índice USD por año-mes para lookup O(1)
        self._usd_idx: dict = {}
        if not self.df_usd.empty and "usd_oficial" in self.df_usd.columns:
            for _, row in self.df_usd.iterrows():
                ym = row["fecha"].strftime("%Y-%m")
                self._usd_idx[ym] = float(row["usd_oficial"])

        # USD de enero 2023 (base) — primer registro disponible
        self.usd_base_2023: float | None = (
            self._usd_idx.get("2023-01")
            or (float(self.df_usd["usd_oficial"].iloc[0]) if not self.df_usd.empty else None)
        )

    def usd_en_fecha(self, fecha) -> float | None:
        """Devuelve el tipo de cambio USD oficial vigente en el mes de `fecha`.

        Estrategia: lookup exacto por YYYY-MM → fallback al mes anterior más
        cercano → fallback a usd_actual.  Nunca usa el USD de hoy para una
        modificación del pasado.
        """
        if not fecha:
            return self.usd_actual
        ym = fecha.strftime("%Y-%m") if hasattr(fecha, "strftime") else str(fecha)[:7]
        if ym in self._usd_idx:
            return self._usd_idx[ym]
        # buscar el mes previo más reciente disponible
        candidatos = [k for k in self._usd_idx if k <= ym]
        if candidatos:
            return self._usd_idx[max(candidatos)]
        return self.usd_actual

    def calcular_variacion_real(self, base, modificaciones):
        monto_original = base.monto_original if base.monto_original and base.monto_original != 0 else None
        if not monto_original:
            return None  # saltar partidas sin crédito original

        # ── Conversión USD del original (al tipo de cambio de enero 2023) ──
        usd_base = self.usd_base_2023 or self.usd_actual
        original_usd = round(monto_original / usd_base, 2) if usd_base else None

        # ── Acumular modificaciones dolarizando cada una a su propio TC ──
        total_mod_nominal = 0.0
        total_mod_usd = 0.0
        for m in (modificaciones or []):
            tc = self.usd_en_fecha(m.fecha_boletin) or usd_base
            total_mod_nominal += m.monto_neto
            total_mod_usd += m.monto_neto / tc

        vigente_actual = (base.monto_vigente or monto_original) + total_mod_nominal
        vigente_usd = (original_usd or 0) + total_mod_usd if original_usd is not None else None

        # ── Valor real deflactado por IPC ──
        valor_real = vigente_actual / self.ipc_acumulado
        var_nominal = ((vigente_actual / monto_original) - 1) * 100
        var_real = ((valor_real / monto_original) - 1) * 100
        licuacion = var_nominal - var_real

        # ── Variación en USD (histórica) ──
        var_usd = None
        if original_usd and vigente_usd is not None and original_usd != 0:
            var_usd = round(((vigente_usd / original_usd) - 1) * 100, 2)

        equivalente_usd = None
        if original_usd is not None:
            equivalente_usd = {
                # Original: al TC de enero 2023
                "monto_original_usd": original_usd,
                "tc_original": round(usd_base, 2),
                "fecha_tc_original": "2023-01",
                # Vigente: suma de modificaciones cada una a su TC histórico
                "monto_vigente_usd": round(vigente_usd, 2) if vigente_usd is not None else None,
                "variacion_usd_pct": var_usd,
                "metodologia": "cada modificación dolarizada al TC del mes de su DA/Decreto",
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

        # TC del mes exacto de la DA/Decreto — no el USD de hoy
        tc_norma = self.usd_en_fecha(fecha_norma)
        monto_neto = modificacion.monto_neto
        monto_neto_usd = round(monto_neto / tc_norma, 2) if tc_norma else None

        return {
            "norma_id": modificacion.norma_id,
            "fecha": fecha_norma.isoformat() if fecha_norma else None,
            "programa_id": modificacion.programa_id,
            "monto_neto": monto_neto,
            "monto_neto_usd": monto_neto_usd,
            "tc_en_fecha_norma": round(tc_norma, 2) if tc_norma else None,
            "ipc_mensual_en_fecha_norma": round(float(ipc_mes), 2) if ipc_mes is not None else None,
            "nota": "USD dolarizado al TC oficial del mes de publicación en BORA",
        }
