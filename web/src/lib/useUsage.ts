import { useEffect, useMemo, useState } from "react";
import {
  collection,
  doc,
  onSnapshot,
  query,
  updateDoc,
  where,
  type Timestamp,
} from "firebase/firestore";

import { db } from "./firebase";
import { localDateKey } from "./format";
import type { DailyDoc, Device, DeviceSummary, KeyTotals, RangeKey } from "./types";
import { RANGE_DAYS } from "./types";

/** A PC that hasn't reported in this long is treated as offline. Reports land
 * every ~3 minutes, so this tolerates one missed cycle. */
const OFFLINE_AFTER_MS = 8 * 60 * 1000;

export function useDevices() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const unsub = onSnapshot(
      collection(db, "devices"),
      (snap) => {
        setDevices(
          snap.docs.map((d) => ({
            id: d.id,
            displayName: (d.get("displayName") as string) ?? d.id,
            hostname: (d.get("hostname") as string) ?? "",
            lastSeenAt: (d.get("lastSeenAt") as Timestamp | null) ?? null,
            enrolledAt: (d.get("enrolledAt") as Timestamp | null) ?? null,
            agentVersion: (d.get("agentVersion") as string | null) ?? null,
            revoked: d.get("revoked") === true,
          }))
        );
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      }
    );
    return unsub;
  }, []);

  return { devices, loading, error };
}

/**
 * Live daily aggregates for every device over the selected range.
 *
 * One listener per device, each bounded by a date-range filter, so a 30-day
 * view over 5 PCs streams ~150 documents rather than every record ever
 * written. Date keys are plain YYYY-MM-DD strings, which sort lexicographically
 * -- that's why a range filter works without a composite index.
 */
export function useDailyData(deviceIds: string[], range: RangeKey) {
  const [byDevice, setByDevice] = useState<Record<string, DailyDoc[]>>({});
  const idsKey = deviceIds.join(",");

  const since = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - (RANGE_DAYS[range] - 1));
    return localDateKey(d);
  }, [range]);

  useEffect(() => {
    if (deviceIds.length === 0) {
      setByDevice({});
      return;
    }

    // Drop data for devices no longer selected so stale rows can't linger.
    setByDevice((prev) => {
      const next: Record<string, DailyDoc[]> = {};
      for (const id of deviceIds) if (prev[id]) next[id] = prev[id]!;
      return next;
    });

    const unsubs = deviceIds.map((deviceId) =>
      onSnapshot(
        query(collection(db, "devices", deviceId, "daily"), where("date", ">=", since)),
        (snap) => {
          const docs: DailyDoc[] = snap.docs.map((d) => ({
            deviceId,
            date: (d.get("date") as string) ?? d.id,
            bytesSent: (d.get("bytesSent") as number) ?? 0,
            bytesReceived: (d.get("bytesReceived") as number) ?? 0,
            activeSeconds: (d.get("activeSeconds") as number) ?? 0,
            domains: (d.get("domains") as Record<string, KeyTotals>) ?? {},
            processes: (d.get("processes") as Record<string, KeyTotals>) ?? {},
            hourly: (d.get("hourly") as Record<string, { s: number; r: number }>) ?? {},
          }));
          setByDevice((prev) => ({ ...prev, [deviceId]: docs }));
        }
      )
    );

    return () => unsubs.forEach((u) => u());
    // idsKey stands in for deviceIds: the array identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, since]);

  return byDevice;
}

export function summarize(
  devices: Device[],
  byDevice: Record<string, DailyDoc[]>,
  nowMs: number
): DeviceSummary[] {
  return devices.map((device) => {
    const docs = byDevice[device.id] ?? [];
    const domains = new Set<string>();
    let bytesSent = 0;
    let bytesReceived = 0;
    let activeSeconds = 0;

    for (const d of docs) {
      bytesSent += d.bytesSent;
      bytesReceived += d.bytesReceived;
      activeSeconds += d.activeSeconds;
      for (const key of Object.keys(d.domains)) domains.add(key);
    }

    const lastSeenMs = device.lastSeenAt ? device.lastSeenAt.toMillis() : 0;
    return {
      device,
      bytesSent,
      bytesReceived,
      activeSeconds,
      domainCount: domains.size,
      online: !device.revoked && lastSeenMs > 0 && nowMs - lastSeenMs < OFFLINE_AFTER_MS,
    };
  });
}

/** Merges per-domain totals across the selected devices and days. */
export function aggregateDomains(
  docs: DailyDoc[],
  field: "domains" | "processes" = "domains"
): Array<{ key: string; sent: number; received: number; seconds: number }> {
  const merged = new Map<string, { sent: number; received: number; seconds: number }>();

  for (const d of docs) {
    for (const [key, totals] of Object.entries(d[field])) {
      const existing = merged.get(key);
      if (existing) {
        existing.sent += totals.s ?? 0;
        existing.received += totals.r ?? 0;
        existing.seconds += totals.secs ?? 0;
      } else {
        merged.set(key, {
          sent: totals.s ?? 0,
          received: totals.r ?? 0,
          seconds: totals.secs ?? 0,
        });
      }
    }
  }

  return [...merged.entries()]
    .map(([key, v]) => ({ key, ...v }))
    .sort((a, b) => b.sent + b.received - (a.sent + a.received));
}

export async function renameDevice(deviceId: string, displayName: string) {
  await updateDoc(doc(db, "devices", deviceId), { displayName });
}
