# archive/debug_scripts_2026/

Scripts de debugging y exploración puntual (scraping del BORA, inspección de
PDFs, chequeos ad-hoc de la base) generados durante el desarrollo. Ninguno es
importado por `app/` ni por `scripts/`, y el `Procfile` solo ejecuta
`uvicorn app.main:app` — o sea, nada de esto corre en producción.

Se movieron acá (2026-07) para limpiar la raíz del repo, en vez de borrarlos,
por si alguno sirve de referencia para depurar el scraper del BORA en el
futuro. Si después de un tiempo nadie los necesita, es seguro borrar esta
carpeta entera.

No incluye `analisis.py` (se queda en la raíz — es una herramienta real de
consultas manuales sobre la base, documentada en su propio docstring).
