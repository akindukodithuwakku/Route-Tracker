import { createHash, timingSafeEqual } from "node:crypto";

export const MAX_RECORDS_PER_BATCH = 2000;
export const MAX_DOMAIN_LENGTH = 253;

export function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

/** Constant-time compare of two hex digests, safe on length mismatch. */
export function hashesMatch(a: string, b: string): boolean {
  const bufA = Buffer.from(a, "hex");
  const bufB = Buffer.from(b, "hex");
  if (bufA.length === 0 || bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

/**
 * Domains arrive from agents and are used as Firestore map keys, so they get
 * normalized hard: Firestore rejects empty keys and reserves the "__" prefix,
 * and stray characters would corrupt the document. Whitelist rather than
 * blacklist -- hostnames, IPv4/IPv6 literals and process names only ever need
 * these characters. Returns null for anything unusable so the caller can drop
 * the record instead of writing garbage.
 */
export function sanitizeKey(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  let value = raw.trim().toLowerCase().replace(/[^a-z0-9._:-]/g, "");
  if (!value || value.startsWith("__")) return null;
  if (!/[a-z0-9]/.test(value)) return null; // reject junk like "..." or "---"
  if (value.length > MAX_DOMAIN_LENGTH) value = value.slice(0, MAX_DOMAIN_LENGTH);
  return value;
}

export function toNonNegativeInt(raw: unknown): number {
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.floor(n);
}

export function toFiniteSeconds(raw: unknown): number {
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n) || n < 0) return 0;
  // A single reporting window can't legitimately exceed a day of activity;
  // clamp so a bad clock on one PC can't distort the totals.
  return Math.min(n, 86400);
}

/**
 * Local date/hour as seen by the reporting PC. The manager thinks in terms of
 * "what happened today on that machine", so buckets follow the agent's clock
 * rather than UTC -- but the server derives them from the UTC timestamp plus a
 * declared offset, so an agent can't pick which day to write into.
 */
export function localDateAndHour(
  isoTimestamp: unknown,
  tzOffsetMinutes: number
): { date: string; hour: number } | null {
  if (typeof isoTimestamp !== "string") return null;
  const utcMs = Date.parse(isoTimestamp);
  if (!Number.isFinite(utcMs)) return null;

  const shifted = new Date(utcMs + tzOffsetMinutes * 60_000);
  return { date: shifted.toISOString().slice(0, 10), hour: shifted.getUTCHours() };
}

export function clampTzOffset(raw: unknown): number {
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n)) return 0;
  return Math.max(-840, Math.min(840, Math.floor(n)));
}

/**
 * Rejects timestamps too far from server time, so a stale queue flush or a
 * forged batch can't silently land in an unrelated date bucket.
 */
export function isPlausibleTimestamp(isoTimestamp: string, nowMs: number): boolean {
  const ms = Date.parse(isoTimestamp);
  if (!Number.isFinite(ms)) return false;
  return ms > nowMs - 7 * 24 * 3600 * 1000 && ms < nowMs + 24 * 3600 * 1000;
}
