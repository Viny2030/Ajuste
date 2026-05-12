import uvicorn
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

# Importaciones de tu estructura de carpetas
from app.database import models, schemas
from app.database.session import SessionLocal, engine
from app.core.engine import AnalizadorPresupuestario
from app.core.viz import generar_grafico_ajuste

# Inicialización de la base de datos
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Monitor de Ajuste Presupuestario (MAP)",
    description="Análisis algorítmico del gasto público: Nominal vs. Real (Base 2023)",
    version="1.2.0"
)


# Dependencia de DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- RUTAS GENERALES ---

@app.get("/", tags=["Home"])
async def root():
    return {
        "sistema": "Monitor de Ajuste Presupuestario",
        "estado": "Online",
        "punto_base": "Enero 2023",
        "servidor_tiempo": datetime.now()
    }


# --- RUTAS DE PRESUPUESTO ---

@app.get("/api/v1/programas", response_model=List[schemas.ProgramaResumen], tags=["Presupuesto"])
def listar_programas(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Retorna los programas cargados desde el CSV de Hacienda."""
    return db.query(models.PresupuestoBase).offset(skip).limit(limit).all()


# --- RUTAS DE ANALÍTICA Y CÁLCULO ---

@app.get("/api/v1/analisis/ajuste/{programa_id}", tags=["Analítica"])
def obtener_ajuste_detalle(programa_id: str, db: Session = Depends(get_db)):
    """Calcula la licuación presupuestaria cruzando BORA e Inflación."""
    base = db.query(models.PresupuestoBase).filter(models.PresupuestoBase.programa_id == programa_id).first()
    if not base:
        raise HTTPException(status_code=404, detail="Programa no detectado en la base 2023")

    # Recuperar modificaciones capturadas por el scraper
    modificaciones = db.query(models.ModificacionPresupuestaria).filter(
        models.ModificacionPresupuestaria.programa_id == programa_id
    ).all()

    engine_calc = AnalizadorPresupuestario(db)
    return engine_calc.calcular_variacion_real(base, modificaciones)


# --- RUTAS DE VISUALIZACIÓN (GRÁFICOS) ---

@app.get("/api/v1/graficos/ajuste/{programa_id}", response_class=HTMLResponse, tags=["Visualización"])
def visualizar_grafico(programa_id: str, db: Session = Depends(get_db)):
    """Genera un gráfico dinámico de barras comparando Original vs. Real."""
    base = db.query(models.PresupuestoBase).filter(models.PresupuestoBase.programa_id == programa_id).first()
    if not base:
        raise HTTPException(status_code=404, detail="No hay datos para graficar")

    # Lógica de cálculo simplificada para el gráfico
    modificaciones = db.query(models.ModificacionPresupuestaria).filter(
        models.ModificacionPresupuestaria.programa_id == programa_id
    ).all()

    calc = AnalizadorPresupuestario(db)
    res = calc.calcular_variacion_real(base, modificaciones)

    # Generar HTML con Plotly
    html_content = generar_grafico_ajuste(
        base.programa_desc,
        base.monto_original,
        base.monto_vigente + sum(m.monto_neto for m in modificaciones),
        res["monto_real_en_moneda_2023"]
    )
    return HTMLResponse(content=html_content)


# --- RUTAS DE SCRAPING ---

@app.get("/api/v1/normativa/recientes", tags=["Scraping"])
def ultimas_normas(db: Session = Depends(get_db)):
    """Muestra los últimos decretos detectados en el Boletín Oficial."""
    return db.query(models.ModificacionPresupuestaria).order_by(
        models.ModificacionPresupuestaria.fecha_boletin.desc()
    ).limit(10).all()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8080, reload=True)
