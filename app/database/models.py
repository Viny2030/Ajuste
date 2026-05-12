# app/database/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class PresupuestoBase(Base):
    """Presupuesto Original Ley 27.701 (2023)"""
    __tablename__ = 'presupuesto_base'
    id = Column(Integer, primary_key=True)
    ejercicio = Column(Integer, default=2023)
    jurisdiccion_id = Column(String)
    jurisdiccion_desc = Column(String)
    programa_id = Column(String)
    programa_desc = Column(String)
    monto_original = Column(Float)  # Crédito Presupuestado
    monto_vigente = Column(Float)   # Crédito al cierre 2023

class ModificacionPresupuestaria(Base):
    """Modificaciones detectadas por el Scraper del BORA"""
    __tablename__ = 'modificaciones'
    id = Column(Integer, primary_key=True)
    norma_id = Column(String)        # Ejemplo: "DECAD-2024-123-APN-JGM"
    fecha_boletin = Column(DateTime)
    programa_id = Column(String)
    aumento = Column(Float, default=0.0)
    reduccion = Column(Float, default=0.0)
    monto_neto = Column(Float)       # aumento - reduccion
