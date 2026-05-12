# app/core/daily_sync.py
import asyncio
from app.scrapers.bora_discovery import BoraScraper
from app.scrapers.pdf_processor import extraer_tabla_presupuesto
import pandas as pd
import requests

async def run_daily_workflow():
    print("🚀 Iniciando workflow diario de monitoreo...")
    
    # 1. Buscar nuevas normas
    scraper = BoraScraper()
    nuevas_normas = await scraper.buscar_decretos(desde="2026-05-10") # Ajustar fecha
    
    for norma in nuevas_normas:
        print(f"📄 Procesando: {norma['titulo']}")
        
        # 2. Descarga del PDF (Simplificado)
        pdf_res = requests.get(norma['url'])
        pdf_path = f"data/raw_pdfs/{norma['titulo'][:20]}.pdf"
        with open(pdf_path, 'wb') as f:
            f.write(pdf_res.content)
            
        # 3. Extracción de datos
        try:
            df_ajuste = extraer_tabla_presupuesto(pdf_path)
            
            if df_ajuste is not None:
                # 4. Integrar lógica de deflactación aquí
                # df_ajuste['monto_real'] = df_ajuste['monto'] / indice_inflacion
                
                # 5. Guardar en Base de Datos
                # df_ajuste.to_sql('ejecucion_presupuestaria', engine, if_exists='append')
                print(f"✅ Datos extraídos de {norma['titulo']}")
        except Exception as e:
            print(f"❌ Error procesando PDF {norma['titulo']}: {e}")

if __name__ == "__main__":
    asyncio.run(run_daily_workflow())
