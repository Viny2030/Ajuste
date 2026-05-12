# Monitor de Ajuste Presupuestario (MAP) 🇦🇷

Este sistema analiza el ajuste del gasto público argentino cruzando los decretos del **Boletín Oficial (BORA)** con el presupuesto original de 2023, ajustado por inflación (IPC) y tipo de cambio.

## 🚀 Características
- **Scraper BORA:** Detección automática de Decretos y Decisiones Administrativas de modificación presupuestaria.
- **Engine Analítico:** Deflactación de montos nominales a moneda constante de Enero 2023.
- **FastAPI:** Endpoints preparados para visualización de datos y comparativa por programa.

## 🛠️ Instalación
1. Clonar el repositorio.
2. Instalar dependencias: `pip install -r requirements.txt`
3. Instalar navegadores para el scraper: `playwright install chromium`
4. Cargar base 2023: `python -m scripts.seed_2023` (Requiere el CSV de Presupuesto Abierto en /data)

## 📊 Arquitectura
El sistema utiliza **Camelot** para el parseo de tablas en PDFs y **Playwright** para la navegación dinámica.