<#
Installs the client agent as an always-on Windows Service.
Run this ON EACH CLIENT PC, from an elevated (Run as Administrator) PowerShell.

Before running: copy config.example.json to config.json and fill in
client_id / api_key (from the manager's config\clients.json) and manager_url.
If the manager is using a self-signed cert, also copy its cert.pem into this
folder and set ca_cert_path in config.json to its path.

What it does:
  1. Creates a venv and installs requirements.txt
  2. Registers "LanUsageMonitorAgent" as a Windows Service, set to auto-start
  3. Configures the service to auto-restart on crash
  4. Starts the service
#>

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator."
    exit 1
}

if (-not (Test-Path ".\config.json")) {
    Write-Error "config.json not found. Copy config.example.json to config.json and fill in client_id/api_key/manager_url first."
    exit 1
}
$cfg = Get-Content ".\config.json" | ConvertFrom-Json
if ($cfg.api_key -eq "PASTE_THE_MATCHING_API_KEY_FROM_manager/config/clients.json") {
    Write-Error "config.json still has placeholder values. Edit it first."
    exit 1
}

Write-Host "== Setting up Python environment ==" -ForegroundColor Cyan
if (-not (Test-Path ".\.venv")) {
    py -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

Write-Host "== Registering Windows Service ==" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" service.py --startup auto install

Write-Host "== Configuring crash auto-restart ==" -ForegroundColor Cyan
sc.exe failure LanUsageMonitorAgent reset= 86400 actions= restart/5000/restart/5000/restart/60000 | Out-Null

Write-Host "== Starting service ==" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" service.py start

Write-Host ""
Write-Host "Done. Check status with: Get-Service LanUsageMonitorAgent" -ForegroundColor Green
Write-Host "Logs: $here\logs\agent.log" -ForegroundColor Yellow
