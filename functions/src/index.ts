/**
 * The only server-side component. Agents talk to these two endpoints and
 * nothing else; Firestore rules deny agent SDK access entirely, so a key
 * lifted off a client PC can submit that PC's own usage and do nothing more.
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
import { getFirestore, FieldValue, Timestamp } from "firebase-admin/firestore";
import { randomBytes } from "node:crypto";

import {
  MAX_RECORDS_PER_BATCH,
  clampTzOffset,
  hashesMatch,
  isPlausibleTimestamp,
  localDateAndHour,
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

/** Turns accumulated counters into Firestore sentinels for a merge write. */
function toIncrements(bucket: Record<string, DomainTotals>) {
  const out: Record<string, unknown> = {};
  for (const [key, totals] of Object.entries(bucket)) {
    out[key] = {
      s: FieldValue.increment(totals.s),
      r: FieldValue.increment(totals.r),
      secs: FieldValue.increment(totals.secs),
    };
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

    const batch = db.batch();

    // Only write fields this batch actually carried: a report that omits
    // agent_version must not erase the version we already know.
    const deviceUpdate: Record<string, unknown> = {
      lastSeenAt: Timestamp.now(),
      tzOffsetMinutes: tzOffset,
    };
    if (typeof body.agent_version === "string" && body.agent_version) {
      deviceUpdate.agentVersion = body.agent_version.slice(0, 32);
    }
    batch.update(deviceRef, deviceUpdate);

    for (const [date, agg] of byDate) {
      const hourlyIncrements: Record<string, unknown> = {};
      for (const [hour, totals] of Object.entries(agg.hourly)) {
        hourlyIncrements[hour] = {
          s: FieldValue.increment(totals.s),
          r: FieldValue.increment(totals.r),
        };
      }

      batch.set(
        deviceRef.collection("daily").doc(date),
        {
          date,
          bytesSent: FieldValue.increment(agg.bytesSent),
          bytesReceived: FieldValue.increment(agg.bytesReceived),
          activeSeconds: FieldValue.increment(agg.activeSeconds),
          domains: toIncrements(agg.domains),
          processes: toIncrements(agg.processes),
          hourly: hourlyIncrements,
          updatedAt: Timestamp.now(),
        },
        { merge: true }
      );
    }

    await batch.commit();

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
