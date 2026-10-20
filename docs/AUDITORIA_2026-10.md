# Auditoría MAP — octubre 2026

Revisión de cifras, mecanismos y motores de actualización del Monitor de Ajuste
Presupuestario (`ajuste.ecosistematransparencia.com.ar`), hecha el 2026-10-20.

## 1. Cifras: recalculadas en forma independiente contra BCRA v4.0

| Cifra publicada | Recalculada | Estado |
|---|---|---|
| Deflactor 2026/2023 = 5,8385 (IPC hasta 2026-09, 9 meses) | 5,83849 | ✅ |
| Deflactor 2024 = 3,20 · 2025 = 4,54 | 3,197 · 4,536 | ✅ |
| TC promedio 2023 = 295,21 · 2026 = 1449,09 | 295,21 · 1449,09 | ✅ |
| Variación real gasto total = −35,4 % | 26,12 / 40,41 billones ($ 2023) | ✅ |
| Licuación, jurisdicciones comparables: +251,3 % nominal / −39,8 % real | 3,513 / 5,8385 | ✅ |
| Vigente 2026 = $152,47 billones | coincide con MECON al 07/10 | ✅ |

Desde ahora esto lo repite todos los días `scripts/verificar_cifras.py`
(workflow **Verificar cifras publicadas**).

## 2. Hallazgos en los motores de actualización

| # | Prioridad | Hallazgo | Estado |
|---|---|---|---|
| 1 | 🔴 | La API BCRA v4.0 devuelve como máximo `limit` registros (1000 por defecto, tope 3000) empezando por el más nuevo. La serie diaria del TC desde 2023 tenía 921: hacia feb-2027 se perdían en silencio los primeros meses de 2023 y todas las variaciones en USD quedaban mal. | **Corregido** (paginación con offset en `engine._fetch_bcra_v4` y `main._fetch_bcra_v4_async`) |
| 2 | 🔴 | `discover_bora.py` reescribe `data/nuevas_das.json` completo: si el BORA responde a medias, las normas ya encontradas desaparecen. `DESDE` está fijo en 14/04/2026, así que el período a revisar crece todos los días. | **Mitigado**: `data/das_acumulado.json` (workflow **Acumular DAs descubiertas**) nunca borra. Falta decidir si la API sirve el acumulado. |
| 3 | 🟠 | Unidades contradictorias: `actualizar_presupuesto.py` multiplica ×1.000.000 (correcto: da los $152 billones publicados), `sync_modificaciones.py` multiplica ×1.000 → queda 1000 veces abajo. No está en el circuito de producción. | Pendiente |
| 4 | 🟠 | `daily_sync.py` sólo corre en la tarea programada de Windows y escribe en `normas_jgm`/`modificaciones`, que el sitio no lee. Un fallo transitorio al bajar un PDF marca la norma como NORMATIVA para siempre. `--re-linkear` usa `STRFTIME` (sólo SQLite). | Pendiente |
| 5 | 🟠 | `seed_macro_indices.py` borra y confirma antes de descargar: si argentinadatos falla, la tabla queda vacía. El respaldo CSV usa el dólar **minorista** BNA y la fuente principal el **mayorista** BCRA (id 5). | Pendiente |
| 6 | 🟡 | `daily_budget.yml`: `git push \|\| true` esconde fallas; la carga de 2026 no se aborta ante un ZIP incompleto. `monthly_macro.yml` y `scraper_social.yml` no declaran `permissions: contents: write`. | Mitigado (el control 4 del verificador detecta caídas >10 %) |
| 7 | ⚪ | `actualizar_presupuesto.py` (commit 494f205) usa `COPY ... FORMAT text` con `csv.writer`: si una descripción trajera tabulador, comillas o barra invertida, el formato text no entiende las comillas del CSV y la fila se guardaría alterada o el COPY fallaría (con rollback, sin pérdida). **Hoy no pasa**: ninguna de las 72.859 filas de 2026 tiene esos caracteres. Riesgo latente; conviene `FORMAT csv`. | Pendiente |
| 8 | 🟡 | `/api/v1/macro/series` usa TC minorista (id 4); todo lo demás usa mayorista (id 5). | Pendiente |
| 9 | 🟡 | Respaldos viejos: `_RESPALDO_TC[2025] = 1180` (real 1244,26); constantes muertas en `main.py` (10,53, `IPC_POR_ANIO_FALLBACK`) y `engine.py` (9,6368). El encabezado dice "MAP v2.1" y la API 2.3.1. | Pendiente |
| 10 | ⚪ | En el repo: `debug_scripts_2026/`, `.idea/`, CSV de 38 MB; `setup_scheduler.ps1` con rutas fijas. | Pendiente |

### Advertencias de método (no son errores)

* **Vigente 2026 no es final.** En el último trimestre suelen salir DA y DNU que lo
  amplían; hoy el recorte real aparece más grande de lo que va a terminar siendo.
* **Precios 2026 con 9 meses.** El promedio de precios del año en curso usa los meses
  publicados; el crédito se gasta en todo el año, así que el recorte aparece algo más chico.
  Los dos sesgos van en sentidos opuestos: conviene rotular "2026 a la fecha".
* **Ranking.** Los primeros puestos (−99,7 %) son en su mayoría programas reorganizados,
  no recortes. Conviene un umbral mínimo o una marca de "posible reclasificación".

## 3. Despliegues

El dashboard usa sólo rutas relativas (`/api/v1/...`), así que el cambio de dominios no lo
afecta. El dominio viejo `ajuste-production.up.railway.app` da **404**: cualquier
consumidor que todavía lo use queda roto. `Mapa_Transparencia/main.py` tiene ese dominio
como valor por defecto; funciona porque la variable `AJUSTE_API_URL` está cargada.

Variables vigentes:

| Variable | URL |
|---|---|
| `IRI_API_URL` | https://monitor.ecosistematransparencia.com.ar |
| `CONTRATOS_API_URL` | https://comprar.ecosistematransparencia.com.ar |
| `CONTRATOS_V2_API_URL` | https://contratos.ecosistematransparencia.com.ar |
| `EJECUTIVO_API_URL` | https://jefatura.ecosistematransparencia.com.ar |
| `JUSTICIA_API_URL` | https://justicia.ecosistematransparencia.com.ar |
| `LEGISTATIVO_API_URL` | https://legislativo.ecosistematransparencia.com.ar |
| `AJUSTE_API_URL` | https://ajuste.ecosistematransparencia.com.ar |
| `DECLA_API_URL` | https://decla.ecosistematransparencia.com.ar |
| `CORRUPCION_ESTRATEGICA_API_URL` | https://corrupcionestrategica-production.up.railway.app |
| `MEACI_API_URL`, `SENADORES_API_URL` | **no figuran en la lista**; el Mapa las usa |

El workflow **Verificar cifras publicadas** toma `AJUSTE_API_URL` de las variables del
repositorio (Settings → Variables → Actions) y, si no está, usa el dominio de arriba.

## 4. Archivos nuevos

| Archivo | Qué hace |
|---|---|
| `scripts/verificar_cifras.py` | Recalcula deflactor y TC contra el BCRA, controla frescura del IPC, caída del total 2026 y normas perdidas. Sale con 1 si algo falla. Sólo biblioteca estándar. |
| `.github/workflows/verificar_cifras.yml` | Lo corre todos los días a las 10:30 ART; si falla, abre o comenta un issue. |
| `.github/scripts/acumular_das.py` | Suma `nuevas_das.json` a `das_acumulado.json` sin borrar nunca. |
| `.github/workflows/acumular_das.yml` | Lo corre después de cada **Daily BORA Discovery** exitoso. |
| `data/das_acumulado.json` | Línea de base: las 5 normas descubiertas al 2026-10-20 (`primera_vez_visto` = fecha de creación del acumulado, no de publicación). |
| `data/verificacion/ultima_verificacion.json` | Última verificación sana; el control de caída del total compara contra esto. |
| `tests/test_auditoria.py` | 7 tests sin red de los dos scripts. |
