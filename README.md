# Monitor de Ajuste Presupuestario (MAP) 🇦🇷

Este sistema analiza el ajuste del gasto público argentino cruzando los decretos del **Boletín Oficial (BORA)** con el presupuesto original de 2023, ajustado por inflación (IPC) y tipo de cambio.

## 🚀 Características
- **Scraper BORA:** Detección automática de Decretos y Decisiones Administrativas de modificación presupuestaria, vía los endpoints AJAX del propio sitio del BORA (sin navegador headless).
- **Engine Analítico:** Deflactación de montos nominales a moneda constante de Enero 2023, con datos macro (IPC y tipo de cambio) de la API oficial del BCRA (v4.0).
- **FastAPI:** Endpoints preparados para visualización de datos y comparativa por programa.

## 🛠️ Instalación
1. Clonar el repositorio.
2. Instalar dependencias: `pip install -r requirements.txt`
3. Cargar base 2023: `python -m scripts.seed_2023` (Requiere el CSV de Presupuesto Abierto en /data)

## 📊 Arquitectura
- **Scraping del BORA:** `httpx` contra los endpoints AJAX internos del sitio (`/seccion/actualizar/primera`), sin necesidad de un navegador — ver `app/scrapers/bora_scraper.py` para el detalle de la estrategia.
- **Parseo de PDFs (Anexos de las DAs):** `pdfplumber` como estrategia principal, con fallback a extracción por regex — ver `app/core/pdf_processor.py`.
- **Datos macro:** API oficial del BCRA v4.0 (`api.bcra.gob.ar/estadisticas/v4.0/monetarias`), pública y sin autenticación — ver `app/core/engine.py`.
