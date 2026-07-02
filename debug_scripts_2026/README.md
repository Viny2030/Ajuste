# archive/scripts_exploratorios_2026/

Scripts exploratorios y de una sola vez que vivían dentro de `scripts/`:
pruebas puntuales contra la API del BORA/Infoleg (`test_*.py`), visores
manuales de avisos/normas (`ver_*.py`), un diagnóstico de sectores, un
descargador manual con IDs de BORA hardcodeados, y una copia vieja de
`discover_bora.py` (la que realmente usa el workflow de GitHub Actions
`daily_discover.yml` vive en `.github/scripts/discover_bora.py`, no acá).

Ninguno de estos es importado por `app/` ni referenciado por ningún workflow
de `.github/workflows/`. Se movieron acá (2026-07) para limpiar `scripts/`,
que ahora solo tiene los módulos con uso real:

  - seed_2023.py, seed_2026.py, seed_presupuesto_base.py,
    seed_macro_indices.py — cargan datos base
  - ingest_presupuesto_2026.py, load_2026_to_db.py — ingesta del crédito 2026
  - sync_modificaciones.py — sincroniza crédito vigente 2024/2025/2026
  - migrations/, social/ — submódulos activos

Ver también `archive/debug_scripts_2026/` para los que estaban sueltos en
la raíz del repo (mismo criterio, distinta tanda de limpieza).
