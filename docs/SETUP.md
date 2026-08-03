# LAN Internet Usage Monitor -- Setup Guide

Tracks each client PC's internet usage (domains visited, time spent,
bandwidth) and reports it to one manager PC's localhost dashboard.

## How it works

- Each **client PC** runs an agent (Windows Service) that watches its own
  network traffic locally, reads the domain name out of the TLS handshake
  (SNI) or HTTP `Host:` header, counts bytes per domain, and every 30s sends
  a batch to the manager over HTTPS.
- The **manager PC** runs a small server (Windows Service) that receives
  those batches, stores them in a local SQLite database, and serves a
  dashboard at `https://<manager>:8443/`.
- Only the manager PC receives data; nothing is sent anywhere else. The
  manager's firewall rule restricts inbound connections to your LAN subnet.
- If the manager is briefly unreachable, each agent queues its reports to
  disk and retries with backoff -- no data is lost.

**Before deploying:** monitoring traffic metadata from these PCs should be
disclosed to whoever uses them, per your organization's IT/monitoring
policy. This tool doesn't do anything to hide its own presence -- it
installs as a normally-visible Windows Service and an Add/Remove Programs
entry.

## Requirements

- 6 Windows PCs on the same LAN (1 manager + 5 clients), all reachable from
  each other.
- Administrator rights on all 6 PCs (Windows Services + the packet-capture
  driver both require it). No Python installation needed -- the two
  installers below are fully self-contained.
- The manager PC should have a static IP or DHCP reservation, since every
  client agent points at it by address.

## 1. Install on the manager PC

Copy **`ManagerSetup.exe`** to the manager PC and run it.

1. It will prompt for admin rights (UAC) -- accept.
2. Fill in:
   - **Install to** -- default `C:\Program Files\LAN Usage Monitor\Manager` is fine.
   - **Allow connections from LAN subnet** -- auto-detected (e.g. `192.168.1.0/24`);
     adjust if your LAN uses a different range.
3. Click **Install**. It generates a TLS cert, registers the
   `LanUsageMonitorManager` Windows Service (auto-start, auto-restart on
   crash), opens a firewall rule scoped to your LAN subnet, and starts it.
4. When it finishes, note the two paths it shows you:
   - The dashboard URL (`https://localhost:8443/`)
   - `config\clients.json` -- has 5 auto-generated entries (`pc1`..`pc5`),
     each with a random `api_key`. Open it and rename `display_name` to
     something recognizable if you like (e.g. "Front Desk"). You'll copy
     each `client_id`/`api_key` pair to the matching client PC.
5. Also grab `certs\cert.pem` from the install folder -- copy it somewhere
   reachable from each client PC (USB stick or network share; it's a public
   cert, not a secret).
6. Confirm it's running: browse to `https://<manager-ip>:8443/`. Expect a
   browser warning about the self-signed cert (safe to accept on your own
   LAN tool) and an empty dashboard (no client PCs have reported yet).

## 2. Install on each client PC (repeat 5x)

Copy **`ClientAgentSetup.exe`** to the client PC and run it.

1. It will prompt for admin rights (UAC) -- accept.
2. Fill in:
   - **Install to** -- default is fine.
   - **Manager address** -- `<manager-ip>:8443`.
   - **Client ID** -- `pc1` through `pc5`, matching an entry in the
     manager's `clients.json` (use a different one per PC).
   - **API key** -- the matching `api_key` from the manager's `clients.json`.
   - **Manager cert.pem** -- browse to the `cert.pem` you copied from the
     manager. You can skip this, but then the agent won't verify it's really
     talking to your manager (fine on a trusted LAN, not recommended
     otherwise).
3. Click **Install**. It registers the `LanUsageMonitorAgent` Windows
   Service (auto-start, auto-restart on crash) and starts it immediately.

Within ~30-60 seconds of real browsing on that PC, you should see it appear
"online" on the manager's dashboard with domains and bandwidth showing up.

## 3. Using the dashboard

Open `https://<manager-ip>:8443/` from any browser on the LAN (including the
manager PC itself, via `https://localhost:8443/`). It shows:

- A card per client PC: online/offline, data downloaded/uploaded, active
  time, distinct sites visited -- click a card to drill into just that PC.
- A bandwidth-over-time chart and a top-sites-by-bandwidth list for whatever
  is selected (all PCs, or one).
- A range picker: Today / 24h / 7 days / 30 days.

The page auto-refreshes every 10 seconds.

## Adding more computers later

The dashboard isn't hard-limited to 5 -- to add a 6th (or more) client PC:

1. On the manager PC, open `C:\Program Files\LAN Usage Monitor\Manager\config\clients.json`
   and add a new entry, e.g.:
   ```json
   { "client_id": "pc6", "display_name": "New Desk", "api_key": "<make up a long random string>" }
   ```
2. Restart the manager service so it picks up the new entry:
   ```powershell
   Restart-Service LanUsageMonitorManager
   ```
3. Run `ClientAgentSetup.exe` on the new PC using `pc6` and that `api_key`,
   same as section 2.

## Known limitations (v1)

- **QUIC (HTTP/3) traffic**, used by some Chrome/YouTube/Google connections
  over UDP port 443, doesn't expose SNI the same way TCP TLS does. That
  traffic is still counted for bandwidth but attributed by reverse-DNS of
  the destination IP (or shown as a bare IP if reverse-DNS has no record)
  rather than a clean domain name. Most sites still fall back to/also use
  regular TCP, so this mainly affects precision, not totals.
- **Process attribution** (which app made the connection) is best-effort:
  the OS connection table is sampled every ~2s rather than per-packet, so
  very short-lived connections may show no process name.
- The manager dashboard itself is served over the same HTTPS port as the
  ingest API; there's no separate login for the dashboard yet -- anyone who
  can reach port 8443 on your LAN can view it. If that's not acceptable,
  say so and we can add a login step.

## Maintenance

- **Rotate the TLS cert:** run `"C:\Program Files\LAN Usage Monitor\Manager\LanUsageMonitorManager.exe" gencert`
  (elevated), then copy the new `cert.pem` to every client PC (no other
  changes needed).
- **Check service status:** `Get-Service LanUsageMonitorManager` /
  `Get-Service LanUsageMonitorAgent`. Logs are in each install folder's
  `logs\` subfolder (agent) or console output (manager, via Event Viewer ->
  Windows Logs -> Application).
- **Uninstall:** use Windows' "Add or remove programs" -- both show up as
  "LAN Usage Monitor - Manager" / "LAN Usage Monitor - Agent". This stops
  and removes the service, the firewall rule (manager only), and the
  Add/Remove Programs entry itself; it leaves the install folder (config,
  database, logs) in place in case you want to keep the history -- delete
  it manually if not.

## Advanced: installing from source instead

If you'd rather run from source (e.g. to modify the code), the `manager/`
and `client_agent/` folders are plain Python projects with their own
`install_service.ps1` scripts that do the same install steps without
needing the prebuilt `.exe`s -- see the comments at the top of each script.
This requires Python 3.10+ on the target PC.
