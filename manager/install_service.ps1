<#
Installs the manager as an always-on Windows Service.
Run this ON THE MANAGER PC, from an elevated (Run as Administrator) PowerShell.

What it does:
  1. Creates a venv and installs requirements.txt
  2. Generates a self-signed TLS cert if one doesn't exist yet
  3. Registers "LanUsageMonitorManager" as a Windows Service, set to auto-start
  4. Configures the service to auto-restart on crash
  5. Opens an inbound firewall rule for the report port, restricted to a LAN subnet
  6. Starts the service
#>

param(
    [string]$LanSubnet = "192.168.1.0/24"  # adjust to match your actual LAN
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator."
    exit 1
}

Write-Host "== Setting up Python environment ==" -ForegroundColor Cyan
if (-not (Test-Path ".\.venv")) {
    py -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt

Write-Host "== TLS certificate ==" -ForegroundColor Cyan
if (-not (Test-Path ".\certs\cert.pem")) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" } | Select-Object -First 1 -ExpandProperty IPAddress)
    & ".\.venv\Scripts\python.exe" generate_cert.py $ip
} else {
    Write-Host "cert.pem already exists, skipping"
}

Write-Host "== Registering Windows Service ==" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" service.py --startup auto install

Write-Host "== Configuring crash auto-restart ==" -ForegroundColor Cyan
sc.exe failure LanUsageMonitorManager reset= 86400 actions= restart/5000/restart/5000/restart/60000 | Out-Null

Write-Host "== Firewall rule (inbound, LAN only, port from config\server.json) ==" -ForegroundColor Cyan
$serverCfg = Get-Content ".\config\server.json" -ErrorAction SilentlyContinue | ConvertFrom-Json
$port = if ($serverCfg) { $serverCfg.port } else { 8443 }
Remove-NetFirewallRule -DisplayName "LAN Usage Monitor - Manager" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "LAN Usage Monitor - Manager" -Direction Inbound -Protocol TCP `
    -LocalPort $port -RemoteAddress $LanSubnet -Action Allow | Out-Null
Write-Host "Firewall: allowing inbound TCP/$port only from $LanSubnet"

Write-Host "== Starting service ==" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" service.py start

Write-Host ""
Write-Host "Done. Dashboard: https://localhost:$port/  (or http:// if no cert was generated)" -ForegroundColor Green
Write-Host "Client API keys are in config\clients.json -- copy each client_id/api_key pair to the matching PC's client_agent\config.json" -ForegroundColor Yellow
