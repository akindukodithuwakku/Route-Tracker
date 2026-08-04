import { formatBytes, formatDuration, formatRelativeTime } from "../lib/format";
import type { DeviceSummary } from "../lib/types";

interface Props {
  summaries: DeviceSummary[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onRequestRemove: (deviceId: string) => void;
}

export function DeviceGrid({
  summaries,
  selectedId,
  onSelect,
  onRequestRemove,
}: Props) {
  const totals = summaries.reduce(
    (acc, s) => ({
      sent: acc.sent + s.bytesSent,
      received: acc.received + s.bytesReceived,
      seconds: acc.seconds + s.activeSeconds,
      online: acc.online + (s.online ? 1 : 0),
    }),
    { sent: 0, received: 0, seconds: 0, online: 0 }
  );

  return (
    <div className="device-grid">
      <button
        type="button"
        className="device-card"
        aria-pressed={selectedId === null}
        onClick={() => onSelect(null)}
      >
        <div className="head">
          <span className="name">All PCs</span>
        </div>
        <div className="metric">
          <span>Downloaded</span>
          <b>{formatBytes(totals.received)}</b>
        </div>
        <div className="metric">
          <span>Uploaded</span>
          <b>{formatBytes(totals.sent)}</b>
        </div>
        <div className="metric">
          <span>Active time</span>
          <b>{formatDuration(totals.seconds)}</b>
        </div>
        <div className="seen">
          {totals.online} of {summaries.length} online
        </div>
      </button>

      {summaries.map((s) => (
        <div
          key={s.device.id}
          className={`device-card${selectedId === s.device.id ? " is-selected" : ""}`}
        >
          <button
            type="button"
            className="device-card-main"
            aria-pressed={selectedId === s.device.id}
            onClick={() => onSelect(s.device.id)}
          >
            <div className="head">
              <span
                className={`dot ${s.device.revoked ? "revoked" : s.online ? "online" : "offline"}`}
                title={s.device.revoked ? "revoked" : s.online ? "online" : "offline"}
              />
              <span className="name" title={s.device.hostname}>
                {s.device.displayName}
              </span>
            </div>
            <div className="metric">
              <span>Downloaded</span>
              <b>{formatBytes(s.bytesReceived)}</b>
            </div>
            <div className="metric">
              <span>Uploaded</span>
              <b>{formatBytes(s.bytesSent)}</b>
            </div>
            <div className="metric">
              <span>Active time</span>
              <b>{formatDuration(s.activeSeconds)}</b>
            </div>
            <div className="metric">
              <span>Sites</span>
              <b>{s.domainCount}</b>
            </div>
            <div className="seen">
              {s.device.revoked
                ? "Revoked"
                : `Last report ${formatRelativeTime(
                    s.device.lastSeenAt ? s.device.lastSeenAt.toDate() : null
                  )}`}
            </div>
          </button>
          <button
            type="button"
            className="device-remove-btn"
            aria-label={`Remove ${s.device.displayName}`}
            onClick={(e) => {
              e.stopPropagation();
              onRequestRemove(s.device.id);
            }}
          >
            Remove
          </button>
        </div>
      ))}
    </div>
  );
}
