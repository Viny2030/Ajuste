# scripts/restaurar_no_versionados.ps1
#
# Recupera en disco los archivos que dejaron de versionarse en la limpieza de
# la auditoría 2026-10 (commit "chore: limpieza del repo ...").
#
# Por qué hace falta: cuando un commit saca archivos del versionado, git los
# BORRA de cada copia local al hacer pull o merge de ese commit. Siguen en el
# historial; este script los vuelve a escribir en disco SIN volver a
# versionarlos (quedan cubiertos por .gitignore). Si ya están, no los toca.
#
# Uso, desde cualquier PC después del pull:
#   powershell -ExecutionPolicy Bypass -File scripts\restaurar_no_versionados.ps1

$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo

# Último commit en el que estaban versionados
$Origen = "a3996d6"
$Rutas = @(
    ".idea",
    "debug_scripts_2026",
    "data/presupuesto_base/credito_anual_2026_completo.csv",
    "data/presupuesto_base/credito_anual_2026_jgm.csv"
)

git cat-file -e "$Origen^{commit}" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "No se encontró el commit $Origen. Corré primero: git fetch origin"
    exit 1
}

# Archivo por archivo: una carpeta puede existir a medias (p. ej. .idea con
# sólo workspace.xml, que nunca estuvo versionado).
$archivos = git ls-tree -r --name-only $Origen -- $Rutas
$restaurados = 0
foreach ($a in $archivos) {
    if (Test-Path -LiteralPath $a) { continue }
    # --worktree sin --staged: escribe en disco y no lo vuelve a versionar
    git restore --source=$Origen --worktree -- $a
    if ($LASTEXITCODE -ne 0) { Write-Host "  ERROR: $a"; exit 1 }
    $restaurados++
}
Write-Host "$restaurados de $($archivos.Count) archivos restaurados (el resto ya estaba)."

$pendientes = git status --porcelain -- $Rutas
if ($pendientes) {
    Write-Host "Atención: git ve cambios en estas rutas:`n$pendientes"
    exit 1
}
Write-Host "Listo: archivos en disco y fuera del versionado."
