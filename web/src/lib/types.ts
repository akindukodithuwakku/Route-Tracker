import type { Timestamp } from "firebase/firestore";

/** Per-key totals. Field names are short because they repeat once per domain
 * per day and Firestore bills document size. */
export interface KeyTotals {
  s: number;
  r: number;
  secs: number;
}

export interface Device {
  id: string;
  displayName: string;
  hostname: string;
  lastSeenAt: Timestamp | null;
  enrolledAt: Timestamp | null;
  agentVersion: string | null;
  revoked: boolean;
}

/** One document per device per local day, written by the report function. */
export interface DailyDoc {
  deviceId: string;
  date: string; // YYYY-MM-DD, in the reporting PC's local time
  bytesSent: number;
  bytesReceived: number;
  activeSeconds: number;
  domains: Record<string, KeyTotals>;
  processes: Record<string, KeyTotals>;
  hourly: Record<string, { s: number; r: number }>;
}

export interface DeviceSummary {
  device: Device;
  bytesSent: number;
  bytesReceived: number;
  activeSeconds: number;
  domainCount: number;
  online: boolean;
}

export type RangeKey = "today" | "7d" | "30d";

export const RANGE_LABELS: Record<RangeKey, string> = {
  today: "Today",
  "7d": "7 days",
  "30d": "30 days",
};

export const RANGE_DAYS: Record<RangeKey, number> = {
  today: 1,
  "7d": 7,
  "30d": 30,
};
