import pandas as pd
from app.database.session import SessionLocal, engine
from app.database import models


def cargar_presupuesto_2023(csv_path: str):
    models.Base.metadata.create_all(bind=engine)
    print(f"📖 Leyendo archivo: {csv_path}...")

    try:
        df = pd.read_csv(csv_path)

        # Detectar automáticamente la columna del año (ejercicio)
        # Algunos datasets usan 'impacto_presupuestario_anio' y otros 'ejercicio_presupuestario'
        col_anio = 'impacto_presupuestario_anio' if 'impacto_presupuestario_anio' in df.columns else 'ejercicio_presupuestario'

        if col_anio not in df.columns:
            print(f"❌ ERROR: No se encontró columna de año. Columnas disponibles: {df.columns.tolist()}")
            return

        # Filtrar ejercicio 2023
        df_2023 = df[df[col_anio] == 2023]

        db = SessionLocal()
        print(f"🚀 Insertando {len(df_2023)} registros...")

        for _, row in df_2023.iterrows():
            item = models.PresupuestoBase(
                ejercicio=2023,
                jurisdiccion_id=str(row['jurisdiccion_id']),
                jurisdiccion_desc=row['jurisdiccion_desc'],
                programa_id=str(row['programa_id']),
                programa_desc=row['programa_desc'],
                monto_original=float(row['credito_presupuestado']),
                monto_vigente=float(row['credito_vigente'])
            )
            db.add(item)

        db.commit()
        db.close()
        print("✅ Carga inicial 2023 completada con éxito.")

    except Exception as e:
        print(f"❌ Error durante la carga: {e}")


if __name__ == "__main__":
    cargar_presupuesto_2023("data/presupuesto_2023.csv")
