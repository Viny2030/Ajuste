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
| 2 | 🔴 | `discover_bora.py` reescribe `data/nuevas_das.json` completo: si el BORA responde a medias, las normas ya encontradas desaparecen. `DESDE` está fijo en 14/04/2026, así que el período a revisar crece todos los días. | **Corregido**: `/api/v1/normativa/` sirve también `das_acumulado.json` (deduplicado por `norma_id`); el descubrimiento usa ventana móvil de 30 días (`DESDE` opcional vía la variable de repo `BORA_DESDE`). |
| 3 | 🟠 | Unidades contradictorias: `actualizar_presupuesto.py` multiplica ×1.000.000 (correcto: da los $152 billones publicados), `sync_modificaciones.py` multiplica ×1.000 → queda 1000 veces abajo. No está en el circuito de producción. | **Corregido**: MECON publica en millones de pesos (sus totales ×1.000.000 coinciden con lo publicado: $40,41 billones en 2023, $152,47 en 2026). Sus ZIP no traen columna `unidad` (la agrega `ingest_presupuesto_2026.py` en sus CSV; si viene, se respeta). El control que cubre a MECON es `control_magnitud()`: si el total de un ejercicio sale fuera de $10–$10.000 billones (por ejemplo, si pasaran a publicar en miles), la carga se corta en `actualizar_presupuesto.py` y en `sync_modificaciones.py`, sin opción de forzarla. También dejaba positivos los montos negativos (el regex borraba el signo). |
| 4 | 🟠 | `daily_sync.py` sólo corre en la tarea programada de Windows y escribe en `normas_jgm`/`modificaciones`, que el sitio no lee. Un fallo transitorio al bajar un PDF marca la norma como NORMATIVA para siempre. `--re-linkear` usa `STRFTIME` (sólo SQLite). | **Corregido**: sin PDF la norma no se guarda y se reintenta; `--re-linkear` probado en Postgres. Sigue sin estar en el circuito del sitio (decisión de producto). Las normas que ya quedaron NORMATIVA por esa causa no se pueden distinguir de las legítimas. |
| 5 | 🟠 | `seed_macro_indices.py` borra y confirma antes de descargar: si argentinadatos falla, la tabla queda vacía. El respaldo CSV usa el dólar **minorista** BNA y la fuente principal el **mayorista** BCRA (id 5). | **Corregido**: descarga todo primero y reemplaza cada fuente en su propia transacción; si una falla conserva sus datos y sale con error. El CSV ahora trae `TC_mayorista_bcra` (misma serie que la fuente principal) y el deflactor lo prefiere: con sólo el CSV da 5,8385 / 295,21 / 1449,09, idéntico a lo publicado. (No se usó el mayorista de argentinadatos: en dic-2023/ene-2024 trae 368 en vez de 642.) |
| 6 | 🟡 | `daily_budget.yml`: `git push \|\| true` esconde fallas; la carga de 2026 no se aborta ante un ZIP incompleto. `monthly_macro.yml` y `scraper_social.yml` no declaran `permissions: contents: write`. | **Corregido**: push con reintentos y en rojo si falla; `actualizar_presupuesto.py` aborta antes del DELETE si el vigente cae >10 % o las filas >20 % (`--forzar` para cargar igual) y verifica la unidad; permisos explícitos en `monthly_macro` y `scraper_social`. |
| 7 | ⚪ | `actualizar_presupuesto.py` (commit 494f205) usa `COPY ... FORMAT text` con `csv.writer`: si una descripción trajera tabulador, comillas o barra invertida, el formato text no entiende las comillas del CSV y la fila se guardaría alterada o el COPY fallaría (con rollback, sin pérdida). **Hoy no pasa**: ninguna de las 72.859 filas de 2026 tiene esos caracteres. Riesgo latente; conviene `FORMAT csv`. | **Corregido**: `COPY ... FORMAT csv`. Probado en Postgres 16: la versión anterior alteraba comillas y barras y fallaba con tabuladores; la nueva los guarda idénticos. Se agregó INSERT por lotes para SQLite (local). |
| 8 | 🟡 | `/api/v1/macro/series` usa TC minorista (id 4); todo lo demás usa mayorista (id 5). | **Corregido**: id 5. |
| 9 | 🟡 | Respaldos viejos: `_RESPALDO_TC[2025] = 1180` (real 1244,26); constantes muertas en `main.py` (10,53, `IPC_POR_ANIO_FALLBACK`) y `engine.py` (9,6368). El encabezado dice "MAP v2.1" y la API 2.3.1. | **Corregido**: respaldos recalculados el 2026-10-20; constantes muertas quitadas; versión única `APP_VERSION = 2.4.0` y el dashboard la lee de `/api/v1/status`. |
| 10 | ⚪ | En el repo: `debug_scripts_2026/`, `.idea/`, CSV de 38 MB; `setup_scheduler.ps1` con rutas fijas. | **Corregido**: `.idea/`, `debug_scripts_2026/` y los dos CSV regenerables dejan de versionarse (siguen en disco y en el historial; achicar el repo requeriría reescribir el historial, no se hizo). `setup_scheduler.ps1` usa rutas relativas y además tenía un error: la redirección `>> log` se le pasaba a python.exe como argumento, así que la tarea nunca habría corrido; ahora pasa por `cmd.exe`. |

### Advertencias de método (no son errores) — resueltas en el dashboard

* **Vigente 2026 no es final.** En el último trimestre suelen salir DA y DNU que lo
  amplían; hoy el recorte real aparece más grande de lo que va a terminar siendo.
* **Precios 2026 con 9 meses.** El promedio de precios del año en curso usa los meses
  publicados; el crédito se gasta en todo el año, así que el recorte aparece algo más chico.
  Los dos sesgos van en sentidos opuestos. **Hecho:** el KPI dice "vigente 2026 a la fecha"
  con la explicación al pasar el mouse, y la API la expone en `deflactor.nota_anio_en_curso`.
* **Ranking.** Los primeros puestos (−99,7 %) son en su mayoría programas reorganizados,
  no recortes. **Hecho:** `/analisis/ranking` marca `revisar_reclasificacion` cuando la caída
  nominal supera el 90 % (20 de los 500 en producción) y el dashboard muestra "⚠ revisar".
  No se excluyen: algunos son recortes reales (p. ej. Asistencia Financiera a Provincias).

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
| `tests/test_motores.py` | 7 tests sin red de los arreglos de los puntos 2 a 9. |

## 5. Cierre (2026-10-20)

### Normas marcadas NORMATIVA por una descarga fallida
Se pueden distinguir: si en `data/raw_pdfs/` hay un PDF válido, la tabla vino vacía
(NORMATIVA legítima); si no, fue una descarga fallida. En la base local eran 2:
**DA-24-2025** y **DA-39-2025**. `scripts/reintentar_normativas.py` las reprocesó y quedaron
como GASTO (2 y 1 partidas). Se verificó que cada una era la única DA presupuestaria con
anexo publicada en el BORA ese día, así que el PDF corresponde a esa norma. Base local:
16 GASTO y 15 NORMATIVA (todas con PDF real).

Al reintentarlas apareció la causa de fondo: los descargadores aceptaban cualquier
respuesta de más de 1000 bytes, y Infoleg/BORA devolvían una página HTML con status 200
que se guardaba como `.pdf`. Ahora `app/scrapers/pdf_check.py` exige la cabecera `%PDF`
en los 5 lugares donde se guarda un PDF y en los 3 donde se reutiliza uno ya bajado.

También: el link al BORA de las normas que vienen de Infoleg se armaba como
`/detalleAviso/primera/0/{numero_boletin}/{fecha}`, que no existe; ahora enlaza la
edición del día (`/seccion/primera/{fecha}`).

### daily_sync y el sitio — decisión
`daily_sync` (montos por DA extraídos de los anexos en PDF) queda como **herramienta de
análisis local**. El sitio no publica montos extraídos de PDF sin validación: un error
de lectura atribuido a una norma concreta es caro en un sitio de transparencia, y el
crédito vigente de MECON que ya se publica incluye el efecto de todas las DA. Si más
adelante se quieren mostrar, el camino es publicar sólo las normas que pasen un control
(p. ej. reducción = ampliación en las reasignaciones) y marcar el resto "a revisar".

### Base local (`sql_app.db`) — reconstruida
Tenía 2023 cargado dos veces (259.763 filas contra 131.709 en producción: el total 2023 daba
$80,8 billones y la variación real −69 % en vez de −35,4 %), 2026 desactualizado y las
modificaciones sintéticas `CREDITO-VIGENTE-*` calculadas con el error de unidades del punto 3.
Se rehízo así:

1. `python -m scripts.actualizar_presupuesto --anio 2023 --forzar` (el control de cordura la
   frenó primero, con razón: el total caía exactamente 50 %, la firma del duplicado) y
   `--anio 2026`. Las cuatro bases anuales quedan iguales a producción.
2. `python -m app.core.daily_sync --re-linkear`: 1.697 modificaciones re-apuntadas a las
   filas nuevas (6 sin equivalente).
3. `python -m scripts.sync_modificaciones` con las unidades corregidas.

Verificado: `evolucion-real`, `licuacion`, `por-inciso` y `sector` dan **idéntico** en local y
en producción. Backups (ignorados por git): `sql_app.backup-2026-10-20.db` (antes del
reintento de normas) y `sql_app.backup-antes-rebuild-2026-10-20.db` (antes de la reconstrucción).

### Archivos que dejaron de versionarse y otras PC
Git borra de cada copia local, al hacer `pull`, los archivos que un commit deja de versionar
(`.idea/`, `debug_scripts_2026/` y los dos CSV regenerables). En otra PC se recuperan, sin
volver a versionarlos, con:

    powershell -ExecutionPolicy Bypass -File scriptsestaurar_no_versionados.ps1

Probado simulando otra PC: restaura los 73 archivos y no deja cambios en git; si se corre de
nuevo no hace nada. Railway y GitHub Actions no usan esos archivos.

### GitHub Actions
`actions/checkout` y `actions/setup-python` pasan de v4/v5 (Node 20, deprecado) a v7
(Node 24). Las notas de v7 sólo quitan el input `pip-install` y versiones de Python sin
soporte; ninguno se usa acá.
