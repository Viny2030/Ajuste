# app/database/schemas.py
from pydantic import BaseModel
from typing import Optional

class ProgramaResumen(BaseModel):
    id: int
    ejercicio: int
    jurisdiccion_desc: str
    programa_id: str
    programa_desc: str
    monto_original: float
    monto_vigente: float

    class Config:
        from_attributes = True

class AnalisisAjuste(BaseModel):
    programa: str
    variacion_nominal: float
    variacion_real: float
    licuacion_porcentual: float