# Architecture

## The shape of the system

```
5x Client PC                        Firebase (cloud)              Manager's browser
+-----------------+                 +-------------------+         +------------------+
| Agent (service) |  HTTPS POST     | Cloud Function    |         | React dashboard  |
| - packet capture| --------------> | /enroll  /report  |         | (Firebase Hosting)|
| - SNI -> domain |  every 3 min    +---------+---------+         +--------+---------+
| - retry queue   |                           |                            |
+-----------------+                           v                            |
                                     +-------------------+                 |
                                     |    Firestore      | <---------------+
                                     | devices/{id}      |  realtime reads
                                     |   daily/{date}    |  (after login)
                                     +-------------------+
```

There is no manager PC any more. The dashboard is a static React app; all
server-side work happens in one Cloud Function.

## Why a Cloud Function instead of agents writing Firestore directly

Agents hold a credential that lives on a user's PC, so it must be able to do
as little as possible. The function is the only thing with database write
access -- an agent can submit its own usage and nothing else. Firestore
security rules deny all client SDK writes outright, so a stolen agent key
can't be used to read other PCs' data, delete history, or forge another
device's report.

## Enrollment: one token, not per-PC credentials

The install must be one-time and identical on every PC, so the installer
asks for exactly one value: an **enrollment token**, the same string on all
5 machines.

First run, the agent self-enrolls:

1. Agent POSTs `/enroll` with `{enrollment_token, hostname, machine_guid}`.
2. Function verifies the token against a hash in `config/enrollment`, then
   creates `devices/{deviceId}` using the PC's own hostname as the display
   name, and returns `{device_id, device_key}`.
3. Agent writes those to `credentials.json` and uses them from then on.
   Re-running enrollment with the same `machine_guid` returns the existing
   device instead of creating a duplicate.

Consequences that matter:

- Adding a 6th PC is just "run the installer with the same token" -- it
  appears on the dashboard by itself. No dashboard-side setup per PC.
- The enrollment token is only useful for *adding* a device. It cannot read
  data. It can be rotated from the dashboard without touching enrolled PCs.
- A per-device key is never shared between machines, so revoking one PC
  doesn't affect the others.

## Firestore data model

Reads and writes are both shaped around cost. The naive design (one document
per usage record) would blow through the free tier and make a 30-day view
read tens of thousands of documents.

```
config/enrollment
  tokenHash, tokenUpdatedAt

devices/{deviceId}
  displayName, hostname, machineGuid, keyHash
  enrolledAt, lastSeenAt, agentVersion, revoked

devices/{deviceId}/daily/{YYYY-MM-DD}     <-- one doc per PC per day
  bytesSent, bytesReceived, activeSeconds
  domains:  { "youtube.com": {s, r, secs}, ... }
  processes:{ "chrome.exe":  {s, r, secs}, ... }
  hourly:   { "0".."23":     {s, r} }
```

Each report is **2 writes**: the device doc's `lastSeenAt`, and a merge into
today's daily doc. At a 3-minute interval that's ~4,800 writes/day for 5 PCs,
inside Firestore's 20K/day free quota.

Each dashboard load reads **one doc per PC per day in range** -- 5 docs for
today, 150 for a 30-day view. The hourly buckets live inside the daily doc,
so the bandwidth-over-time chart costs no extra reads.

Field names inside the maps are shortened (`s`/`r`/`secs`) because they repeat
once per domain per day and Firestore bills document size.

## Report interval and cost

3 minutes (configurable per device later). Rough monthly usage for 5 PCs:
~72K function invocations and ~145K Firestore writes. Blaze's included free
quotas are 2M invocations and 20K writes/day, so the expected bill is $0 --
Blaze is required for outbound networking on functions, not because the
workload costs money.
