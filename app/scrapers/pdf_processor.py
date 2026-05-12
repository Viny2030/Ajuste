# app/scrapers/pdf_processor.py
import camelot
import pandas as pd

def extraer_tabla_presupuesto(pdf_path):
    """
    Extrae tablas de anexos presupuestarios.
    Flavor 'lattice' se usa cuando hay líneas de tabla visibles.
    """
    # El presupuesto suele tener tablas en modo 'lattice'
    tablas = camelot.read_pdf(pdf_path, pages='all', flavor='lattice')
    
    tablas_procesadas = []
    
    for i, tabla in enumerate(tablas):
        df = tabla.df
        
        # Limpieza básica: la primera fila suele ser el encabezado
        df.columns = df.iloc[0]
        df = df[1:].reset_index(drop=True)
        
        # Identificar columnas clave (Jurisdicción, Programa, Importe)
        # El BORA usa nombres como 'JUR', 'PRG', 'INCREMENTO', 'REDUCCIÓN'
        df = df.rename(columns=lambda x: x.replace('\n', ' '))
        
        tablas_procesadas.append(df)
        
    return pd.concat(tablas_procesadas) if tablas_procesadas else None

# Ejemplo de uso:
# df_final = extraer_tabla_presupuesto("anexo_decreto_123.pdf")
# print(df_final.head())
