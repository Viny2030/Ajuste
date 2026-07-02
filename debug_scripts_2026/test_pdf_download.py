import sys
sys.path.insert(0, '.')
import asyncio, json

from app.scrapers.bora_discovery import descargar_pdf_norma

async def test():
    with open('data/processed/normas_infoleg_2024.json', encoding='utf-8') as f:
        normas = json.load(f)
    n = normas[0]
    print(f"Intentando: {n['norma_id']} -> {n['url_infoleg']}")
    resultado = await descargar_pdf_norma(n['url_infoleg'], f"data/raw_pdfs/{n['norma_id']}.pdf")
    print(f"Resultado: {resultado}")

asyncio.run(test())
