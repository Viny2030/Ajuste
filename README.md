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

## 🔄 Motores de actualización (GitHub Actions)

| Workflow | Cuándo | Qué hace |
|---|---|---|
| Daily BORA Discovery | 03:00 ART | Busca DA/DNU presupuestarias de los últimos 30 días → `data/nuevas_das.json` |
| Acumular DAs descubiertas | tras el anterior | Suma lo descubierto a `data/das_acumulado.json` (nunca borra) |
| Daily Budget Monitor | 08:00 ART | Recarga el crédito 2026 desde MECON en la Postgres (aborta si el total cae >10 %) |
| Verificar cifras publicadas | 10:30 ART | Recalcula deflactor y TC contra el BCRA y controla la API; si algo falla abre un issue |
| monthly_macro | día 16 | IPC y TC → `data/seeds/macro_indices.csv` (respaldo si el BCRA no responde) |
| Social Scraper | días 1 y 15 | Indicadores sanitarios |

`app/core/daily_sync.py` (montos por DA leídos de los anexos en PDF) es una herramienta
de análisis **local**: el sitio no publica montos extraídos de PDF sin validar.
Auditoría de cifras y mecanismos: [`docs/AUDITORIA_2026-10.md`](docs/AUDITORIA_2026-10.md).

Tests: `python -m pytest tests`

Si después de un `pull` faltan `debug_scripts_2026/`, `.idea/` o los CSV grandes de
`data/presupuesto_base/` (dejaron de versionarse en 2026-10 y git los borra al actualizar):
`powershell -ExecutionPolicy Bypass -File scriptsestaurar_no_versionados.ps1`
