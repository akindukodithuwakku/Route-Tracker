<#
Installs the client agent as an always-on Windows Service, running from
source (Python) instead of the prebuilt ClientAgentSetup.exe.
Run this ON EACH CLIENT PC, from an elevated (Run as Administrator) PowerShell.

Before running: copy config.example.json to config.json and fill in
cloud_base_url / enrollment_token -- both printed together by
scripts/setup-project.js and identical on every PC (see docs/SETUP.md).
There is no per-PC id or key to configure; the agent enrolls itself using
this PC's own hostname on first run.

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
    Write-Error "config.json not found. Copy config.example.json to config.json and fill in cloud_base_url/enrollment_token first."
    exit 1
}
$cfg = Get-Content ".\config.json" | ConvertFrom-Json
if ($cfg.enrollment_token -eq "REPLACE_ME_WITH_TOKEN_FROM_SETUP_SCRIPT") {
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
