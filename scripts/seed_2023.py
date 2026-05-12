# scripts/seed_2023.py
import pandas as pd
from app.database.session import SessionLocal
from app.database.models import PresupuestoBase

def cargar_presupuesto_2023(csv_path):
    """
    Carga el presupuesto 2023 desde los datasets de Hacienda.
    Filtra por ejercicio 2023 para establecer la base de comparación.
    """
    df = pd.read_csv(csv_path)
    
    # Filtrar solo lo necesario para el Monitor
    # Columnas típicas: impacto_presupuestario_anio, jurisdiccion_desc, programa_desc, credito_presupuestado, credito_vigente
    df_2023 = df[df['impacto_presupuestario_anio'] == 2023]
    
    session = SessionLocal()
    
    for _, row in df_2023.iterrows():
        p = PresupuestoBase(
            jurisdiccion_id=row['jurisdiccion_id'],
            jurisdiccion_desc=row['jurisdiccion_desc'],
            programa_id=row['programa_id'],
            programa_desc=row['programa_desc'],
            monto_original=row['credito_presupuestado'],
            monto_vigente=row['credito_vigente']
        )
        session.add(p)
    
    session.commit()
    print("✅ Base 2023 cargada exitosamente.")

if __name__ == "__main__":
    cargar_presupuesto_2023("data/presupuesto_2023.csv")
