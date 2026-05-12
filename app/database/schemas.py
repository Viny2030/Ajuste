# app/database/schemas.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ── Presupuesto Base ──────────────────────────────────────────────

class PartidaResumen(BaseModel):
    id: int
    ejercicio: int
    jurisdiccion_desc: str
    programa_id: str
    programa_desc: str
    inciso_desc: Optional[str] = None
    principal_desc: Optional[str] = None
    fuente_financiamiento_desc: Optional[str] = None
    monto_original: float
    monto_vigente: float

    class Config:
        from_attributes = True


class PartidaDetalle(PartidaResumen):
    entidad_desc: Optional[str] = None
    subprograma_id: Optional[str] = None
    parcial_desc: Optional[str] = None
    subparcial_desc: Optional[str] = None


# ── Normas JGM ───────────────────────────────────────────────────

class NormaResumen(BaseModel):
    id: int
    norma_id: str
    tipo_norma: str
    numero: str
    anio: int
    fecha_publicacion: Optional[datetime]
    titulo: Optional[str]
    tipo_accion: Optional[str]
    monto_total_reduccion: Optional[float]
    monto_total_ampliacion: Optional[float]
    url_bora: Optional[str]

    class Config:
        from_attributes = True


# ── Análisis de Ajuste ────────────────────────────────────────────

class AjustePartida(BaseModel):
    programa_id: str
    programa_desc: str
    jurisdiccion: str
    monto_original_nominal: float
    monto_vigente_nominal: float
    monto_real_en_moneda_2023: float
    variacion_nominal_pct: float
    variacion_real_pct: float
    licuacion_pct: float
    estado_ajuste: str
    factor_ipc_acumulado: float
    equivalente_usd: Optional[dict] = None
    cantidad_modificaciones: int


class ComparativaMacro(BaseModel):
    periodo: str
    gasto_nominal: float
    gasto_real: float
    ipc_acum: float
    usd_oficial: Optional[float] = None


class SerieMacro(BaseModel):
    ipc: List[dict]
    usd_oficial: List[dict]


class CruceNormaInflacion(BaseModel):
    norma_id: str
    fecha: Optional[str]
    programa_id: str
    monto_neto: float
    ipc_mensual_en_fecha_norma: Optional[float]


# ── Filtros / Query params ────────────────────────────────────────

class FiltroAjuste(BaseModel):
    jurisdiccion_id: Optional[str] = None
    inciso_id: Optional[str] = None
    fuente_financiamiento_id: Optional[str] = None
    top_n: int = 20