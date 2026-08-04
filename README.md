# Route Tracker

Tracks internet usage (sites visited, time spent, bandwidth) across a set of
Windows PCs and reports it to a web dashboard you can open from anywhere.

- **Install once per PC.** A single `.exe`, two pasted values, done. Each PC
  enrolls itself and appears on the dashboard automatically.
- **No manager PC.** The backend is Firebase (Cloud Functions + Firestore);
  the dashboard is a static React app on Firebase Hosting.
- **Always running.** The agent is a Windows Service: starts on boot before
  login, auto-restarts on crash, and queues reports to disk when offline.
- **Login required.** Only accounts you explicitly authorize can read data.

## Layout

| Path | What it is |
|---|---|
| [`client_agent/`](client_agent) | Windows agent (Python): packet capture, TLS SNI → domain, durable retry queue, Windows Service wrapper, installer GUI |
| [`functions/`](functions) | Cloud Functions (TypeScript): the `/enroll` and `/report` endpoints — the only thing with database write access |
| [`web/`](web) | Dashboard (React + TypeScript + Vite) |
| [`shared/`](shared) | The usage-record shape the agent sends |
| [`scripts/`](scripts) | One-time project setup (manager login, enrollment token) |
| [`firestore.rules`](firestore.rules) | Access control: manager reads, clients get nothing |

## Getting started

See **[docs/SETUP.md](docs/SETUP.md)** for the full walkthrough, and
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for how the pieces fit
together and why (data model, enrollment design, cost math).

## Local development

```bash
npm install
npm run emulators                      # Firestore + Functions + Auth emulators
npm --prefix web run dev               # dashboard against the emulators
```

Set `VITE_USE_EMULATORS=true` in `web/.env.local` for the dev server to talk
to the emulators instead of your live project.

## A note on scope

This captures traffic metadata (domain names and byte counts), not content.
It does not decrypt anything — domains come from the cleartext portion of the
TLS handshake, the same signal a corporate firewall reads. Whoever uses the
monitored PCs should be told they're monitored, per your organization's
policy; the agent installs visibly as a service and in Add/Remove Programs.
