import { formatBytes, formatDuration, prettyDomain } from "../lib/format";

interface Entry {
  key: string;
  sent: number;
  received: number;
  seconds: number;
}

interface Props {
  entries: Entry[];
  limit?: number;
  emptyHint: string;
}

export function TopDomains({ entries, limit = 12, emptyHint }: Props) {
  if (entries.length === 0) {
    return <div className="empty">{emptyHint}</div>;
  }

  const shown = entries.slice(0, limit);
  const max = Math.max(...shown.map((e) => e.sent + e.received), 1);

  return (
    <div>
      {shown.map((e) => {
        const total = e.sent + e.received;
        return (
          <div className="domain-row" key={e.key}>
            <div className="line">
              <span className="label" title={e.key}>
                {prettyDomain(e.key)}
              </span>
              <span className="value">
                {formatBytes(total)}
                {e.seconds > 0 && ` · ${formatDuration(e.seconds)}`}
              </span>
            </div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${(total / max) * 100}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
