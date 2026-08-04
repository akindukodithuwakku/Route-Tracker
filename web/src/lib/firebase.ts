/**
 * Firebase client setup. The config values here are not secrets -- they
 * identify the project to Google's servers and are visible in any web app's
 * bundle. Access control lives in firestore.rules, not in hiding these.
 *
 * Values come from build-time env vars so the same source builds against a
 * different project without edits. `npm run setup` prints them; see
 * docs/SETUP.md.
 */
import { initializeApp } from "firebase/app";
import { getAuth, connectAuthEmulator } from "firebase/auth";
import { getFirestore, connectFirestoreEmulator } from "firebase/firestore";
import { getFunctions, connectFunctionsEmulator } from "firebase/functions";

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const missingConfig = !config.apiKey || !config.projectId;

const app = initializeApp(
  missingConfig ? { apiKey: "missing", projectId: "missing" } : config
);

export const auth = getAuth(app);
export const db = getFirestore(app);
/** Must match functions/src/index.ts region("asia-south1"). */
export const functions = getFunctions(app, "asia-south1");

// `npm run dev` against the emulators instead of the live project.
if (import.meta.env.VITE_USE_EMULATORS === "true") {
  connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
  connectFirestoreEmulator(db, "127.0.0.1", 8080);
  connectFunctionsEmulator(functions, "127.0.0.1", 5001);
}
