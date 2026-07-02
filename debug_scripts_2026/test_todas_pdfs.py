import sys, asyncio, json, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
from app.scrapers.bora_discovery import descargar_pdf_norma

async def test():
    with open('data/processed/normas_infoleg_2024.json', encoding='utf-8') as f:
        normas = json.load(f)
    for n in normas:
        destino = f"data/raw_pdfs/{n['norma_id']}.pdf"
        fecha_fmt = n['fecha_boletin'].replace('-', '')
        resultado = await descargar_pdf_norma(n['url_infoleg'], destino, fecha_boletin=fecha_fmt)
        status = 'OK' if resultado else 'FALLO'
        print(f"[{status}] {n['norma_id']}")

asyncio.run(test())
