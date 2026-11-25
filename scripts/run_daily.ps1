# Execução diária (Windows). Para agendar às 07:15 todos os dias, rode UMA vez num PowerShell:
#
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$PWD\scripts\run_daily.ps1`""
#   $t = New-ScheduledTaskTrigger -Daily -At 07:15
#   $s = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable
#   Register-ScheduledTask -TaskName "jobhunter" -Action $a -Trigger $t -Settings $s
#
# -WakeToRun acorda o PC da suspensão; -StartWhenAvailable roda depois se o PC estava desligado.

Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path data\logs | Out-Null
if (Test-Path .env) {
  Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2; [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim())
  }
}
if (Test-Path .venv\Scripts\Activate.ps1) { . .venv\Scripts\Activate.ps1 }
python -m jobhunter run *>> ("data\logs\" + (Get-Date -Format yyyy-MM-dd) + ".log")
