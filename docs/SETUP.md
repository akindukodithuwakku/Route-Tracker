# Route Tracker -- Setup Guide

Tracks each client PC's internet usage (domains visited, time spent,
bandwidth) and reports it to a web dashboard you can open from anywhere.

This is a one-time cloud setup (about 15 minutes) followed by a one-time
install on each client PC (about 1 minute each, no configuration to type in
beyond a single pasted token).

## How it works

- Each **client PC** runs an agent (Windows Service) that watches its own
  network traffic locally, reads the domain name out of the TLS handshake
  (SNI) or HTTP `Host:` header, counts bytes per domain, and every 3 minutes
  sends a batch to the cloud.
- The **cloud backend** (Firebase: Cloud Functions + Firestore) receives
  those batches and stores them. There is no manager PC to install or keep
  running -- the cloud replaces it.
- The **dashboard** is a web page (Firebase Hosting) you open in any
  browser, from anywhere, after signing in.
- If the cloud is briefly unreachable, each agent queues its reports to disk
  and retries with backoff -- no data is lost.

**Before deploying:** monitoring traffic metadata from these PCs should be
disclosed to whoever uses them, per your organization's IT/monitoring
policy. This tool doesn't do anything to hide its own presence -- it
installs as a normally-visible Windows Service and an Add/Remove Programs
entry.

## Requirements

- A Google account, for a free Firebase project.
- A card on file for Firebase's Blaze (pay-as-you-go) plan -- **required to
  enable Cloud Functions at all**, but expected cost is $0/month for 5 PCs
  reporting every 3 minutes (see docs/ARCHITECTURE.md for the numbers).
  Firebase will not silently charge you beyond the plan's usage-based
  pricing; there's no fixed fee.
- Node.js 20+ on the machine you use to deploy (not needed on the client
  PCs -- their installer is a self-contained `.exe`).
- Administrator rights on each of the 5 client PCs (the packet-capture
  driver requires it).

## 1. Create the Firebase project

1. Go to [console.firebase.google.com](https://console.firebase.google.com)
   and create a project.
2. **Upgrade to the Blaze plan** (Project settings -> Usage and billing) --
   this is required for Cloud Functions to make outbound network calls at
   all, even within the free quota.
3. **Firestore Database** -> Create database -> Production mode -> pick a
   region (e.g. `asia-south1` / Mumbai -- must match `functions/src/index.ts`'s
   `region("asia-south1")` if you change it).
4. **Authentication** -> Sign-in method -> enable **Email/Password**.
5. **Project settings -> General -> Your apps** -> Add app -> Web. Copy the
   `firebaseConfig` values shown -- you'll need them in step 3.

## 2. Point this repo at your project

```bash
npm install
npx firebase login
npx firebase use --add          # pick your project, alias it "default"
```

Copy `web/.env.example` to `web/.env.local` and fill in the values from
step 1.5:

```bash
cp web/.env.example web/.env.local
```

## 3. Deploy

```bash
npm run deploy
```

This builds the Cloud Functions and the dashboard, then deploys Firestore
rules, Functions, and Hosting. First deploy takes a few minutes.

## 4. Run the one-time setup script

```bash
node scripts/setup-project.js --email you@example.com
```

This creates your manager login (prints a generated password if you didn't
pass `--password`), authorizes it to view the dashboard, and prints an
**enrollment token** -- shown once, save it now. It's the same token pasted
into every client PC's installer.

## 5. Install the agent on each client PC (repeat 5x)

Copy **`ClientAgentSetup.exe`** (from `release/`, or built via
`client_agent`'s own build steps below) to the client PC and run it.

1. It will prompt for admin rights (UAC) -- accept.
2. Paste the two values from step 4's output:
   - **Cloud endpoint URL** -- looks like
     `https://asia-south1-your-project.cloudfunctions.net`
   - **Enrollment token**
3. Click **Install**. It registers the `LanUsageMonitorAgent` Windows
   Service (auto-start, auto-restart on crash) and starts it immediately.

That PC enrolls itself using its own hostname and appears on the dashboard
within a few minutes -- there is nothing to configure per PC beyond those
two pasted values, and they're identical on all 5 machines.

## 6. Open the dashboard

`https://your-project.web.app` (or the URL `npm run deploy` printed). Sign
in with the email/password from step 4.

## Adding more computers later

Run `ClientAgentSetup.exe` on the new PC with the **same** cloud URL and
enrollment token used before. It appears on the dashboard by itself --
nothing to do on the dashboard side, no dashboard-side setup per PC.

## Building ClientAgentSetup.exe yourself

If you're not using a prebuilt release, build it from `client_agent/`:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python -m PyInstaller service.py --name LanUsageMonitorAgent --onedir --console `
    --paths "..\shared" --hidden-import win32timezone --hidden-import win32com --collect-all pydivert --noconfirm
.\.venv\Scripts\python -m PyInstaller installer_gui.py --name ClientAgentSetup --onefile --windowed `
    --add-data "dist\LanUsageMonitorAgent;payload" --hidden-import win32timezone --noconfirm
```

`dist\ClientAgentSetup.exe` is the installer to copy to each PC.

## Known limitations (v1)

- **QUIC (HTTP/3) traffic**, used by some Chrome/YouTube/Google connections
  over UDP port 443, doesn't expose SNI the same way TCP TLS does. That
  traffic is still counted for bandwidth and attributed from DNS answers
  snooped on the PC (free, local) or reverse-DNS of the destination IP, or
  shown as a bare IP if neither has a name. DNS-over-HTTPS/TLS lookups are
  not visible on port 53, so those flows may stay as IPs/PTR names. Most
  sites still fall back to/also use regular TCP + classic DNS.
- **Process attribution** (which app made the connection) is best-effort:
  the OS connection table is sampled every ~2s rather than per-packet, so
  very short-lived connections may show no process name.
- Data is aggregated per local day per PC (not per individual visit), so the
  dashboard shows "how much/how long per site per day", not a minute-by-minute
  browsing history.

## Maintenance

- **30-day retention (automatic):** the scheduled Cloud Function
  `purgeOldDaily` runs every night at 03:15 Asia/Colombo and deletes each
  device's `daily/{YYYY-MM-DD}` documents older than 30 days. The dashboard
  day picker matches that window. No manual cleanup is required.
- **Rename a PC on the dashboard:** click its card; renaming support can be
  wired up via `renameDevice()` in `web/src/lib/useUsage.ts` -- it's exposed
  but not yet bound to a UI control in v1.
- **Remove a PC** from the dashboard: open the PC card and click **Remove**,
  then confirm. That permanently deletes the device and all of its usage
  history. The agent on that PC will be rejected until reinstalled with the
  enrollment token.
- **Revoke a PC** without deleting history: set `revoked: true` on its
  `devices/{id}` document (Firestore console). Its reports will then be
  rejected with 403 until un-revoked.
- **Rotate the enrollment token:**
  `node scripts/setup-project.js --email you@example.com --rotate-token`.
  Already-enrolled PCs are unaffected (they keep their own device_id/key);
  this only changes what a *new* install needs to paste.
- **Add another manager account:**
  `node scripts/setup-project.js --email someone-else@example.com`.
- **Uninstall the agent:** Windows "Add or remove programs" -> "Route
  Tracker - Agent". Stops and removes the service and the Add/Remove
  Programs entry; leaves `config.json`/`credentials.json`/logs in the
  install folder in case you want them -- delete the folder manually if not.
