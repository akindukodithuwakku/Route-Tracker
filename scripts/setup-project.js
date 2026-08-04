#!/usr/bin/env node
/**
 * One-time project setup, run after `firebase deploy`.
 *
 * Does the three things that otherwise mean clicking around the Firebase
 * console and hand-editing documents:
 *   1. creates (or finds) the manager's login account
 *   2. authorizes it by writing managers/{uid} -- being signed in is not
 *      enough to read data, see firestore.rules
 *   3. generates the enrollment token, stores only its hash, and prints the
 *      token once for pasting into the installers
 *
 * Usage:
 *   node scripts/setup-project.js --email you@example.com [--password ...] [--rotate-token]
 *
 * Requires GOOGLE_APPLICATION_CREDENTIALS pointing at a service-account key,
 * or an active `gcloud auth application-default login` session.
 */

const crypto = require("node:crypto");
const { parseArgs } = require("node:util");

const { initializeApp, applicationDefault } = require("firebase-admin/app");
const { getFirestore, Timestamp } = require("firebase-admin/firestore");
const { getAuth } = require("firebase-admin/auth");

const { values } = parseArgs({
  options: {
    email: { type: "string" },
    password: { type: "string" },
    project: { type: "string" },
    "rotate-token": { type: "boolean", default: false },
  },
});

if (!values.email) {
  console.error("Usage: node scripts/setup-project.js --email you@example.com [--password ...] [--rotate-token]");
  process.exit(1);
}

const projectId = values.project || process.env.GCLOUD_PROJECT || process.env.GOOGLE_CLOUD_PROJECT;
if (!projectId) {
  console.error("No project id. Pass --project <id> or set GCLOUD_PROJECT.");
  process.exit(1);
}

const sha256 = (v) => crypto.createHash("sha256").update(v, "utf8").digest("hex");

/** Groups of 5 lowercase-alnum chars: still ~103 bits, but a human can retype
 * it off a screen without losing their place. */
function generateEnrollmentToken() {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789"; // no l/o/0/1
  const groups = [];
  for (let g = 0; g < 4; g++) {
    let group = "";
    for (let i = 0; i < 5; i++) {
      group += alphabet[crypto.randomInt(alphabet.length)];
    }
    groups.push(group);
  }
  return groups.join("-");
}

async function main() {
  initializeApp({ credential: applicationDefault(), projectId });
  const db = getFirestore();
  const auth = getAuth();

  // --- 1 + 2: manager account, then authorize it ---
  let user;
  try {
    user = await auth.getUserByEmail(values.email);
    console.log(`Found existing account for ${values.email}`);
  } catch (err) {
    if (err.code !== "auth/user-not-found") throw err;
    const password = values.password || crypto.randomBytes(12).toString("base64url");
    user = await auth.createUser({ email: values.email, password, emailVerified: true });
    console.log(`Created account ${values.email}`);
    if (!values.password) {
      console.log(`Generated password: ${password}`);
      console.log("  (change it from the dashboard, or via the Firebase console)");
    }
  }

  await db.doc(`managers/${user.uid}`).set(
    { email: values.email, addedAt: Timestamp.now() },
    { merge: true }
  );
  console.log(`Authorized ${values.email} to view the dashboard`);

  // --- 3: enrollment token ---
  const tokenDoc = db.doc("config/enrollment");
  const existing = await tokenDoc.get();

  if (existing.exists && !values["rotate-token"]) {
    console.log("\nEnrollment token already configured (only its hash is stored, so it");
    console.log("cannot be shown again). Re-run with --rotate-token to issue a new one.");
    console.log("Rotating does NOT affect already-enrolled PCs.");
  } else {
    const token = generateEnrollmentToken();
    await tokenDoc.set({ tokenHash: sha256(token), tokenUpdatedAt: Timestamp.now() });
    console.log("\n" + "=".repeat(52));
    console.log("  ENROLLMENT TOKEN (shown once, save it now)");
    console.log("");
    console.log("      " + token);
    console.log("");
    console.log("  Paste this into ClientAgentSetup.exe on every PC.");
    console.log("  The same token is used on all of them.");
    console.log("=".repeat(52));
  }

  console.log(`\nDashboard: https://${projectId}.web.app`);
}

main().catch((err) => {
  console.error("\nSetup failed:", err.message || err);
  process.exit(1);
});
