# setup_scheduler.ps1
# Registra una tarea programada en Windows para correr daily_sync a las 3:00 AM
# Uso: powershell -ExecutionPolicy Bypass -File setup_scheduler.ps1

$TaskName    = "MAP_DailySync"
$PythonExe   = "C:\Users\ASUS\PycharmProjects\Ajuste\.venv\Scripts\python.exe"
$ProjectDir  = "C:\Users\ASUS\PycharmProjects\Ajuste"
$LogDir      = "$ProjectDir\logs"
$LogFile     = "$LogDir\daily_sync.log"

# Crear carpeta de logs si no existe
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "Carpeta de logs creada: $LogDir"
}

# Comando a ejecutar
$Arguments = "-m app.core.daily_sync --solo-recientes >> `"$LogFile`" 2>&1"

# Acción de la tarea
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $Arguments `
    -WorkingDirectory $ProjectDir

# Trigger: todos los días a las 3:00 AM
$Trigger = New-ScheduledTaskTrigger -Daily -At "03:00"

# Configuración: correr aunque el usuario no esté logueado, no despertar el equipo
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

# Registrar la tarea (requiere permisos de administrador)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tarea anterior eliminada."
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Highest `
    -Description "Sincronización diaria MAP — Infoleg + BORA (solo recientes)" `
    | Out-Null

Write-Host ""
Write-Host "✅ Tarea '$TaskName' registrada correctamente."
Write-Host "   Horario : todos los dias a las 03:00 AM"
Write-Host "   Python  : $PythonExe"
Write-Host "   Proyecto: $ProjectDir"
Write-Host "   Log     : $LogFile"
Write-Host ""
Write-Host "Para verificar:"
Write-Host "   Get-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Para correr manualmente ahora:"
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Para ver el log:"
Write-Host "   Get-Content '$LogFile' -Tail 50"