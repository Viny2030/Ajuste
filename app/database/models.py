# app/database/models.py
"""
Modelos extendidos:
- PresupuestoBase: partida con detalle de inciso/ppal/parcial/subparcial
- NormaJGM: Decisiones Administrativas del Jefe de Gabinete
- ModificacionPresupuestaria: vínculo norma ↔ partida ↔ monto
- MacroIndice: caché local de IPC y USD
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class PresupuestoBase(Base):
    """
    Presupuesto Original Ley 27.701 (2023) a nivel de partida completa.
    Nomenclatura: Jurisdicción / SAF / Programa / SubP / Proyecto / Act /
                  Obra / Inciso / PPal / Parcial / Subparcial / Fuente / Ubicación Geográfica
    """
    __tablename__ = "presupuesto_base"

    id = Column(Integer, primary_key=True, index=True)
    ejercicio = Column(Integer, default=2023)

    # Clasificación administrativa
    jurisdiccion_id = Column(String(3))
    jurisdiccion_desc = Column(String(200))
    entidad_id = Column(String(5))
    entidad_desc = Column(String(200))

    # Programa / Subprograma / Proyecto / Actividad / Obra
    programa_id = Column(String(5))
    programa_desc = Column(String(300))
    subprograma_id = Column(String(5), nullable=True)
    proyecto_id = Column(String(5), nullable=True)
    actividad_id = Column(String(5), nullable=True)
    obra_id = Column(String(5), nullable=True)

   # Clasificación económica (la más granular para análisis de ajuste)
    inciso_id = Column(String(5))
    inciso_desc = Column(String(500))
    principal_id = Column(String(5))
    principal_desc = Column(String(500))
    parcial_id = Column(String(10), nullable=True)
    parcial_desc = Column(String(500), nullable=True)
    subparcial_id = Column(String(10), nullable=True)
    subparcial_desc = Column(String(500), nullable=True)
    # Financiamiento
    fuente_financiamiento_id = Column(String(10))
    fuente_financiamiento_desc = Column(String(500))
    ubicacion_geografica_id = Column(String(10), nullable=True)

    # Montos
    monto_original = Column(Float)   # Crédito Inicial
    monto_vigente = Column(Float)    # Crédito Vigente al cierre del ejercicio

    # Relaciones
    modificaciones = relationship("ModificacionPresupuestaria", back_populates="partida")

    __table_args__ = (
        Index("ix_partida_completa", "jurisdiccion_id", "programa_id", "inciso_id", "principal_id"),
    )


# Valores posibles de tipo_da
# "GASTO"      → tiene tabla de modificaciones de crédito → procesar normalmente
# "DEUDA"      → modifica deuda/recursos, sin tabla de gasto → no reintentar
# "RECURSOS"   → modifica recursos, sin tabla de gasto → no reintentar
# "NORMATIVA"  → DA normativa/organizativa sin tabla → no reintentar
# "DESCONOCIDO"→ todavía no clasificada (default para nuevas inserciones)
TIPO_DA_GASTO      = "GASTO"
TIPO_DA_DEUDA      = "DEUDA"
TIPO_DA_RECURSOS   = "RECURSOS"
TIPO_DA_NORMATIVA  = "NORMATIVA"
TIPO_DA_DESCONOCIDO = "DESCONOCIDO"


class NormaJGM(Base):
    """
    Decisiones Administrativas del Jefe de Gabinete de Ministros
    y Decretos que modifican el presupuesto (scrapeadas del BORA).
    """
    __tablename__ = "normas_jgm"

    id = Column(Integer, primary_key=True, index=True)
    norma_id = Column(String(80), unique=True, index=True)
    jurisdiccion_id = Column(String(3), nullable=True, index=True)
    tipo_norma = Column(String(20))
    numero = Column(String(20))
    anio = Column(Integer)
    fecha_publicacion = Column(DateTime)
    titulo = Column(Text)
    url_bora = Column(Text)
    pdf_url = Column(Text, nullable=True)
    pdf_hash = Column(String(64), nullable=True)
    texto_resumen = Column(Text, nullable=True)
    tipo_accion = Column(String(20), nullable=True)
    monto_total_reduccion = Column(Float, nullable=True)
    monto_total_ampliacion = Column(Float, nullable=True)

    # Clasificación del tipo de DA para evitar reintentar PDFs sin tabla de gasto.
    # Ver constantes TIPO_DA_* arriba.
    tipo_da = Column(String(20), nullable=True, default=TIPO_DA_DESCONOCIDO)

    # Relaciones
    modificaciones = relationship("ModificacionPresupuestaria", back_populates="norma")


class ModificacionPresupuestaria(Base):
    """
    Vinculación granular norma ↔ programa presupuestario ↔ monto.
    Una norma puede modificar N programas.
    La granularidad es jurisdiccion + programa (nivel que publican las DAs).
    """
    __tablename__ = "modificaciones"

    id = Column(Integer, primary_key=True, index=True)

    # FK a norma
    norma_db_id = Column(Integer, ForeignKey("normas_jgm.id"), nullable=True)
    norma_id = Column(String(80), index=True)
    fecha_boletin = Column(DateTime, index=True)

    # FK a partida (opcional — solo si se puede resolver a partida granular)
    partida_id = Column(Integer, ForeignKey("presupuesto_base.id"), nullable=True)

    # Clasificación administrativa desnormalizada
    jurisdiccion_id = Column(String(3), nullable=True, index=True)
    programa_id = Column(String(5), index=True)

    # Detalle de inciso/ppal para análisis por tipo de gasto
    inciso_id = Column(String(2), nullable=True)
    principal_id = Column(String(3), nullable=True)

    # Montos
    aumento = Column(Float, default=0.0)
    reduccion = Column(Float, default=0.0)
    monto_neto = Column(Float)

    # Relaciones
    norma = relationship("NormaJGM", back_populates="modificaciones")
    partida = relationship("PresupuestoBase", back_populates="modificaciones")

    __table_args__ = (
        Index("ix_mod_programa_fecha", "programa_id", "fecha_boletin"),
        Index("ix_mod_jur_prog", "jurisdiccion_id", "programa_id"),
    )


class MacroIndice(Base):
    """
    Caché local de índices macroeconómicos (IPC y USD) descargados del BCRA.
    """
    __tablename__ = "macro_indices"

    id = Column(Integer, primary_key=True)
    fecha = Column(DateTime, index=True)
    indicador = Column(String(30), index=True)
    valor = Column(Float)
    fuente = Column(String(50), default="BCRA")

    __table_args__ = (
        Index("ix_macro_fecha_indicador", "fecha", "indicador", unique=True),
    )
