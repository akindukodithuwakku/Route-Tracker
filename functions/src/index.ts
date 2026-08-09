/**
 * The only server-side component. Agents talk to `/enroll` and `/report`;
 * the dashboard uses `deleteDevice`. A nightly scheduler (`purgeOldDaily`)
 * deletes daily aggregates past the retention window. Firestore rules deny
 * agent SDK access entirely, so a key lifted off a client PC can submit that
 * PC's own usage and do nothing more.
 *
 * Deliberately 1st-gen functions (the `functions.https` namespace), not v2:
 * 1st-gen HTTPS functions get the predictable
 * https://asia-south1-<project>.cloudfunctions.net/<name> URL, which is what
 * gets typed into ClientAgentSetup.exe on every PC. 2nd-gen functions deploy
 * as Cloud Run services with an unpredictable hashed URL instead -- a poor
 * fit for a value a human copies once and every agent hard-codes.
 */

import * as functions from "firebase-functions/v1";
import * as logger from "firebase-functions/logger";
import { initializeApp } from "firebase-admin/app";
import { getFirestore, Timestamp } from "firebase-admin/firestore";
import { randomBytes } from "node:crypto";

import {
  MAX_RECORDS_PER_BATCH,
  RETENTION_DAYS,
  RETENTION_TZ,
  clampTzOffset,
  hashesMatch,
  isPlausibleTimestamp,
  localDateAndHour,
  oldestRetainedDateKey,
  sanitizeKey,
  sha256,
  toFiniteSeconds,
  toNonNegativeInt,
} from "./shared";

initializeApp();
const db = getFirestore();

const ENROLLMENT_DOC = "config/enrollment";

/** Structural subset of express's Response, so no @types/express needed. */
interface HttpResponse {
  status(code: number): HttpResponse;
  json(body: unknown): unknown;
}

type DomainTotals = { s: number; r: number; secs: number };

/** Accumulated per-report so one batch costs a single daily-doc write. */
interface BatchAggregate {
  bytesSent: number;
  bytesReceived: number;
  activeSeconds: number;
  domains: Record<string, DomainTotals>;
  processes: Record<string, DomainTotals>;
  hourly: Record<string, { s: number; r: number }>;
}

function emptyAggregate(): BatchAggregate {
  return { bytesSent: 0, bytesReceived: 0, activeSeconds: 0, domains: {}, processes: {}, hourly: {} };
}

function addTo(
  bucket: Record<string, DomainTotals>,
  key: string,
  sent: number,
  received: number,
  seconds: number
) {
  const existing = bucket[key];
  if (existing) {
    existing.s += sent;
    existing.r += received;
    existing.secs += seconds;
  } else {
    bucket[key] = { s: sent, r: received, secs: seconds };
  }
}

/**
 * Firestore allows at most 500 field transforms (FieldValue.increment, etc.)
 * per document write. A busy PC easily exceeds that (domains × 3 increments),
 * which surfaced as HTTP 500 from /report. So domain/process/hourly maps are
 * merged in a transaction as plain numbers instead of nested increments.
 */
const MAX_MAP_KEYS = 800;

function mergeTotals(
  existing: Record<string, DomainTotals> | undefined,
  delta: Record<string, DomainTotals>
): Record<string, DomainTotals> {
  const out: Record<string, DomainTotals> = {};
  for (const [key, totals] of Object.entries(existing ?? {})) {
    out[key] = {
      s: Number(totals?.s) || 0,
      r: Number(totals?.r) || 0,
      secs: Number(totals?.secs) || 0,
    };
  }
  for (const [key, totals] of Object.entries(delta)) {
    const cur = out[key];
    if (cur) {
      cur.s += totals.s;
      cur.r += totals.r;
      cur.secs += totals.secs;
    } else {
      out[key] = { s: totals.s, r: totals.r, secs: totals.secs };
    }
  }
  return pruneMap(out, MAX_MAP_KEYS);
}

function mergeHourly(
  existing: Record<string, { s: number; r: number }> | undefined,
  delta: Record<string, { s: number; r: number }>
): Record<string, { s: number; r: number }> {
  const out: Record<string, { s: number; r: number }> = {};
  for (const [hour, totals] of Object.entries(existing ?? {})) {
    out[hour] = { s: Number(totals?.s) || 0, r: Number(totals?.r) || 0 };
  }
  for (const [hour, totals] of Object.entries(delta)) {
    const cur = out[hour];
    if (cur) {
      cur.s += totals.s;
      cur.r += totals.r;
    } else {
      out[hour] = { s: totals.s, r: totals.r };
    }
  }
  return out;
}

/** Overflow bucket for pruned map tails. Must not use `__*` (Firestore-reserved). */
const OTHER_KEY = "_other";

/** Keep the heaviest keys; fold the long tail into _other so docs stay small. */
function pruneMap(
  map: Record<string, DomainTotals>,
  maxKeys: number
): Record<string, DomainTotals> {
  const entries = Object.entries(map);
  if (entries.length <= maxKeys) return map;

  entries.sort((a, b) => b[1].s + b[1].r - (a[1].s + a[1].r));
  const kept = entries.filter(([k]) => k !== OTHER_KEY).slice(0, maxKeys - 1);
  const keptKeys = new Set(kept.map(([k]) => k));
  let otherS = 0;
  let otherR = 0;
  let otherSecs = 0;
  for (const [key, totals] of entries) {
    if (keptKeys.has(key)) continue;
    otherS += totals.s;
    otherR += totals.r;
    otherSecs += totals.secs;
  }
  const out: Record<string, DomainTotals> = Object.fromEntries(kept);
  if (otherS || otherR || otherSecs) {
    out[OTHER_KEY] = { s: otherS, r: otherR, secs: otherSecs };
  }
  return out;
}

function fail(res: HttpResponse, status: number, message: string): void {
  res.status(status).json({ error: message });
}

/**
 * POST /enroll  { enrollment_token, hostname, machine_guid }
 *   -> { device_id, device_key }
 *
 * The same token goes on every PC by design -- it only grants the ability to
 * add a device, never to read data. Re-enrolling a known machine_guid returns
 * the existing device so a reinstall doesn't create a duplicate card.
 */
export const enroll = functions
  .region("asia-south1")
  .runWith({ maxInstances: 5 })
  .https.onRequest(async (req, res): Promise<void> => {
    if (req.method !== "POST") return fail(res, 405, "POST only");

    const body = (req.body ?? {}) as Record<string, unknown>;
    const token = typeof body.enrollment_token === "string" ? body.enrollment_token.trim() : "";
    const hostname = typeof body.hostname === "string" ? body.hostname.trim().slice(0, 64) : "";
    const machineGuid =
      typeof body.machine_guid === "string" ? body.machine_guid.trim().slice(0, 128) : "";

    if (!token || !hostname || !machineGuid) {
      return fail(res, 400, "enrollment_token, hostname and machine_guid are required");
    }

    const configSnap = await db.doc(ENROLLMENT_DOC).get();
    const expectedHash = configSnap.get("tokenHash");
    if (typeof expectedHash !== "string" || !expectedHash) {
      logger.error("enrollment token not configured; run scripts/setup-project.js");
      return fail(res, 503, "enrollment not configured on the server");
    }
    if (!hashesMatch(sha256(token), expectedHash)) {
      logger.warn("enrollment rejected: bad token", { hostname });
      return fail(res, 401, "invalid enrollment token");
    }

    const existing = await db
      .collection("devices")
      .where("machineGuid", "==", machineGuid)
      .limit(1)
      .get();

    const deviceKey = randomBytes(32).toString("hex");
    const now = Timestamp.now();
    const existingDoc = existing.docs[0];

    if (existingDoc) {
      // Reinstall on a known machine: rotate its key, keep its history.
      const batch = db.batch();
      batch.update(existingDoc.ref, { hostname, revoked: false, reEnrolledAt: now });
      batch.set(existingDoc.ref.collection("private").doc("auth"), { keyHash: sha256(deviceKey) });
      await batch.commit();

      logger.info("re-enrolled existing device", { deviceId: existingDoc.id, hostname });
      res.json({ device_id: existingDoc.id, device_key: deviceKey });
      return;
    }

    const ref = db.collection("devices").doc();
    const batch = db.batch();
    batch.set(ref, {
      displayName: hostname, // the PC's own name, so there's nothing to configure per PC
      hostname,
      machineGuid,
      enrolledAt: now,
      lastSeenAt: null,
      revoked: false,
    });
    batch.set(ref.collection("private").doc("auth"), { keyHash: sha256(deviceKey) });
    await batch.commit();

    logger.info("enrolled new device", { deviceId: ref.id, hostname });
    res.json({ device_id: ref.id, device_key: deviceKey });
  });

/**
 * POST /report  { device_id, device_key, agent_version, tz_offset_minutes, records[] }
 *
 * Two writes per call regardless of record count: the device's lastSeenAt and
 * one merge into the local-day aggregate. Everything the dashboard renders is
 * precomputed here, so a 30-day view reads ~150 documents instead of ~150k.
 */
export const report = functions
  .region("asia-south1")
  .runWith({ maxInstances: 10 })
  .https.onRequest(async (req, res): Promise<void> => {
    if (req.method !== "POST") return fail(res, 405, "POST only");

    const body = (req.body ?? {}) as Record<string, unknown>;
    const deviceId = typeof body.device_id === "string" ? body.device_id.trim() : "";
    const deviceKey = typeof body.device_key === "string" ? body.device_key.trim() : "";
    const records = Array.isArray(body.records) ? body.records : null;

    if (!deviceId || !deviceKey || !records) {
      return fail(res, 400, "device_id, device_key and records are required");
    }
    if (records.length > MAX_RECORDS_PER_BATCH) {
      return fail(res, 413, `at most ${MAX_RECORDS_PER_BATCH} records per batch`);
    }

    const deviceRef = db.collection("devices").doc(deviceId);
    const snaps = await db.getAll(deviceRef, deviceRef.collection("private").doc("auth"));
    const deviceSnap = snaps[0];
    const authSnap = snaps[1];

    if (!deviceSnap?.exists) return fail(res, 401, "unknown device");

    const keyHash = authSnap?.get("keyHash");
    if (typeof keyHash !== "string" || !hashesMatch(sha256(deviceKey), keyHash)) {
      logger.warn("report rejected: bad device key", { deviceId });
      return fail(res, 401, "invalid device key");
    }
    if (deviceSnap.get("revoked") === true) return fail(res, 403, "device revoked");

    const tzOffset = clampTzOffset(body.tz_offset_minutes);
    const nowMs = Date.now();

    // Keyed by local date, since one batch can straddle midnight.
    const byDate = new Map<string, BatchAggregate>();
    let dropped = 0;

    for (const raw of records) {
      const rec = (raw ?? {}) as Record<string, unknown>;
      const domain = sanitizeKey(rec.domain);
      const endedAt = typeof rec.ended_at === "string" ? rec.ended_at : "";

      if (!domain || !endedAt || !isPlausibleTimestamp(endedAt, nowMs)) {
        dropped++;
        continue;
      }
      const stamp = localDateAndHour(endedAt, tzOffset);
      if (!stamp) {
        dropped++;
        continue;
      }

      const sent = toNonNegativeInt(rec.bytes_sent);
      const received = toNonNegativeInt(rec.bytes_received);
      const seconds = toFiniteSeconds(rec.duration_seconds);
      if (sent === 0 && received === 0) {
        dropped++;
        continue;
      }

      let agg = byDate.get(stamp.date);
      if (!agg) {
        agg = emptyAggregate();
        byDate.set(stamp.date, agg);
      }

      agg.bytesSent += sent;
      agg.bytesReceived += received;
      agg.activeSeconds += seconds;
      addTo(agg.domains, domain, sent, received, seconds);

      const process = sanitizeKey(rec.process_name);
      if (process) addTo(agg.processes, process, sent, received, seconds);

      const hourKey = String(stamp.hour);
      const hourBucket = agg.hourly[hourKey];
      if (hourBucket) {
        hourBucket.s += sent;
        hourBucket.r += received;
      } else {
        agg.hourly[hourKey] = { s: sent, r: received };
      }
    }

    // Only write fields this batch actually carried: a report that omits
    // agent_version must not erase the version we already know.
    const deviceUpdate: Record<string, unknown> = {
      lastSeenAt: Timestamp.now(),
      tzOffsetMinutes: tzOffset,
    };
    if (typeof body.agent_version === "string" && body.agent_version) {
      deviceUpdate.agentVersion = body.agent_version.slice(0, 32);
    }

    try {
      const dates = [...byDate.keys()];
      const dailyRefs = dates.map((date) => deviceRef.collection("daily").doc(date));

      await db.runTransaction(async (tx) => {
        // All reads before writes (Firestore transaction rule).
        const dailySnaps = await Promise.all(dailyRefs.map((ref) => tx.get(ref)));

        tx.update(deviceRef, deviceUpdate);

        for (let i = 0; i < dates.length; i++) {
          const date = dates[i]!;
          const agg = byDate.get(date)!;
          const prev = dailySnaps[i]?.data() ?? {};
          tx.set(
            dailyRefs[i]!,
            {
              date,
              bytesSent: (Number(prev.bytesSent) || 0) + agg.bytesSent,
              bytesReceived: (Number(prev.bytesReceived) || 0) + agg.bytesReceived,
              activeSeconds: (Number(prev.activeSeconds) || 0) + agg.activeSeconds,
              domains: mergeTotals(prev.domains as Record<string, DomainTotals> | undefined, agg.domains),
              processes: mergeTotals(
                prev.processes as Record<string, DomainTotals> | undefined,
                agg.processes
              ),
              hourly: mergeHourly(
                prev.hourly as Record<string, { s: number; r: number }> | undefined,
                agg.hourly
              ),
              updatedAt: Timestamp.now(),
            },
            { merge: true }
          );
        }
      });
    } catch (err) {
      logger.error("report write failed", {
        deviceId,
        dates: [...byDate.keys()],
        domainKeys: [...byDate.values()].reduce((n, a) => n + Object.keys(a.domains).length, 0),
        err: err instanceof Error ? err.message : String(err),
      });
      return fail(res, 500, "internal error writing report");
    }

    if (dropped > 0) logger.info("dropped unusable records", { deviceId, dropped });
    res.json({
      status: "ok",
      accepted: records.length - dropped,
      dropped,
      dates: [...byDate.keys()],
    });
  });

/**
 * Callable from the dashboard. Deletes a device and every related document
 * (daily aggregates, private auth key hash). Only authorized managers may
 * invoke this -- Firestore rules deny client-side deletes entirely.
 */
export const deleteDevice = functions
  .region("asia-south1")
  .runWith({ maxInstances: 5 })
  .https.onCall(async (data, context) => {
    if (!context.auth) {
      throw new functions.https.HttpsError("unauthenticated", "Sign in required");
    }

    const managerSnap = await db.doc(`managers/${context.auth.uid}`).get();
    if (!managerSnap.exists) {
      throw new functions.https.HttpsError("permission-denied", "Not an authorized manager");
    }

    const deviceId =
      typeof data?.deviceId === "string" ? data.deviceId.trim().slice(0, 128) : "";
    if (!deviceId) {
      throw new functions.https.HttpsError("invalid-argument", "deviceId is required");
    }

    const deviceRef = db.collection("devices").doc(deviceId);
    const deviceSnap = await deviceRef.get();
    if (!deviceSnap.exists) {
      throw new functions.https.HttpsError("not-found", "Device not found");
    }

    // recursiveDelete removes the device doc plus daily/ and private/ children.
    await db.recursiveDelete(deviceRef);

    logger.info("deleted device and related data", {
      deviceId,
      hostname: deviceSnap.get("hostname") ?? null,
      by: context.auth.uid,
    });

    return { status: "ok", deviceId };
  });

/**
 * Nightly job: delete per-device daily aggregates older than RETENTION_DAYS.
 * Keeps the dashboard's 30-day window bounded so storage (and reads) don't grow
 * forever. Safe to re-run -- only documents with date < cutoff are removed.
 */
export const purgeOldDaily = functions
  .region("asia-south1")
  .runWith({ timeoutSeconds: 540, memory: "512MB", maxInstances: 1 })
  .pubsub.schedule("every day 03:15")
  .timeZone(RETENTION_TZ)
  .onRun(async () => {
    const cutoff = oldestRetainedDateKey(RETENTION_DAYS, RETENTION_TZ);
    let deleted = 0;
    let devicesScanned = 0;

    const devicesSnap = await db.collection("devices").select().get();
    for (const deviceDoc of devicesSnap.docs) {
      devicesScanned++;
      const dailyCol = deviceDoc.ref.collection("daily");
      // Page through old days; a single PC rarely has more than a few dozen.
      let page = await dailyCol.where("date", "<", cutoff).limit(400).get();
      while (!page.empty) {
        const batch = db.batch();
        for (const docSnap of page.docs) batch.delete(docSnap.ref);
        await batch.commit();
        deleted += page.docs.length;
        if (page.docs.length < 400) break;
        page = await dailyCol.where("date", "<", cutoff).limit(400).get();
      }
    }

    logger.info("purged daily docs past retention", {
      cutoff,
      retentionDays: RETENTION_DAYS,
      devicesScanned,
      deleted,
    });
    return null;
  });
