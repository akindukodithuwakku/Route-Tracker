import { useEffect, useMemo, useState } from "react";
import { onAuthStateChanged, signOut, type User } from "firebase/auth";

import { auth, missingConfig } from "./lib/firebase";
import {
  aggregateDomains,
  deleteDevice,
  summarize,
  useDailyData,
  useDevices,
} from "./lib/useUsage";
import { RANGE_LABELS, type RangeKey } from "./lib/types";
import { LoginScreen } from "./components/LoginScreen";
import { DeviceGrid } from "./components/DeviceGrid";
import { ConfirmDeleteModal } from "./components/ConfirmDeleteModal";
import { TopDomains } from "./components/TopDomains";
import { UsageChart } from "./components/UsageChart";

const RANGES: RangeKey[] = ["today", "7d", "30d"];

function ConfigMissing() {
  return (
    <div className="app">
      <div className="banner warn">
        <strong>Not configured yet.</strong>
        <br />
        This build has no Firebase project attached. Create{" "}
        <code>web/.env.local</code> from <code>web/.env.example</code> with your
        project's values, then rebuild. See <code>docs/SETUP.md</code>.
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);

  useEffect(() => {
    if (missingConfig) {
      setAuthReady(true);
      return;
    }
    return onAuthStateChanged(auth, (u) => {
      setUser(u);
      setAuthReady(true);
    });
  }, []);

  if (missingConfig) return <ConfigMissing />;
  if (!authReady) return <div className="app" />;
  if (!user) return <LoginScreen />;
  return <Dashboard email={user.email ?? ""} />;
}

function Dashboard({ email }: { email: string }) {
  const [range, setRange] = useState<RangeKey>("today");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const { devices, loading, error } = useDevices();

  // Re-render on a timer so "online" and "3m ago" decay without new data.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const deviceIds = useMemo(() => devices.map((d) => d.id), [devices]);
  const byDevice = useDailyData(deviceIds, range);

  const summaries = useMemo(
    () => summarize(devices, byDevice, nowMs),
    [devices, byDevice, nowMs]
  );

  const visibleDocs = useMemo(() => {
    if (selectedId) return byDevice[selectedId] ?? [];
    return Object.values(byDevice).flat();
  }, [byDevice, selectedId]);

  const domains = useMemo(() => aggregateDomains(visibleDocs, "domains"), [visibleDocs]);
  const processes = useMemo(() => aggregateDomains(visibleDocs, "processes"), [visibleDocs]);

  const selectedName = selectedId
    ? devices.find((d) => d.id === selectedId)?.displayName ?? "PC"
    : "All PCs";

  const pendingDeleteName =
    devices.find((d) => d.id === pendingDeleteId)?.displayName ?? "this PC";

  const handleRequestRemove = (deviceId: string) => {
    setDeleteError(null);
    setPendingDeleteId(deviceId);
  };

  const handleConfirmDelete = async () => {
    if (!pendingDeleteId || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deleteDevice(pendingDeleteId);
      if (selectedId === pendingDeleteId) setSelectedId(null);
      setPendingDeleteId(null);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Couldn't remove this PC. Try again.";
      setDeleteError(message);
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Route Tracker</h1>
          <p>
            {devices.length === 0
              ? "No PCs enrolled yet"
              : `${devices.length} PC${devices.length === 1 ? "" : "s"} · ${email}`}
          </p>
        </div>
        <div className="topbar-actions">
          <div className="range-picker" role="group" aria-label="Time range">
            {RANGES.map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={range === key}
                onClick={() => setRange(key)}
              >
                {RANGE_LABELS[key]}
              </button>
            ))}
          </div>
          <button type="button" className="ghost-btn" onClick={() => signOut(auth)}>
            Sign out
          </button>
        </div>
      </header>

      {error && (
        <div className="banner warn">
          <strong>Couldn't load data.</strong>
          <br />
          {error}
          <br />
          If this says "permission denied", your account hasn't been authorized yet
          — run the setup script with <code>--email {email}</code>.
        </div>
      )}

      {!loading && devices.length === 0 && !error && (
        <div className="banner">
          <strong>No PCs are reporting yet.</strong>
          <br />
          Run <code>ClientAgentSetup.exe</code> on a PC and paste the enrollment
          token. It will appear here by itself within a few minutes — no setup
          needed on this end.
        </div>
      )}

      <DeviceGrid
        summaries={summaries}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onRequestRemove={handleRequestRemove}
      />

      <div className="panels">
        <section className="panel">
          <div className="panel-head">
            <h2>Bandwidth</h2>
            <span className="panel-sub">
              {selectedName} · {range === "today" ? "by hour" : "by day"}
            </span>
          </div>
          <UsageChart docs={visibleDocs} range={range} />
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Top sites</h2>
            <span className="panel-sub">{selectedName}</span>
          </div>
          <TopDomains
            entries={domains}
            emptyHint="No sites recorded in this period yet."
          />
        </section>
      </div>

      {processes.length > 0 && (
        <div className="panels" style={{ marginTop: 14 }}>
          <section className="panel">
            <div className="panel-head">
              <h2>Top apps</h2>
              <span className="panel-sub">{selectedName}</span>
            </div>
            <TopDomains
              entries={processes}
              limit={8}
              emptyHint="No app attribution available."
            />
          </section>
        </div>
      )}

      {pendingDeleteId && (
        <ConfirmDeleteModal
          deviceName={pendingDeleteName}
          busy={deleteBusy}
          error={deleteError}
          onCancel={() => {
            if (deleteBusy) return;
            setPendingDeleteId(null);
            setDeleteError(null);
          }}
          onConfirm={() => {
            void handleConfirmDelete();
          }}
        />
      )}
    </div>
  );
}
