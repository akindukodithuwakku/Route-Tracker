import { formatBytes, recentDateKeys } from "../lib/format";
import type { DailyDoc, RangeKey } from "../lib/types";
import { RANGE_DAYS } from "../lib/types";

interface Props {
  docs: DailyDoc[];
  range: RangeKey;
  /** When set, chart shows hourly bars for that single day. */
  selectedDay?: string | null;
}

interface Bar {
  label: string;
  value: number;
  emphasize: boolean;
}

function buildHourlyBars(docs: DailyDoc[], emphasizeCurrentHour: boolean): Bar[] {
  const hours = new Array<number>(24).fill(0);
  for (const d of docs) {
    for (const [hour, totals] of Object.entries(d.hourly)) {
      const h = Number(hour);
      if (Number.isInteger(h) && h >= 0 && h < 24) {
        hours[h] = (hours[h] ?? 0) + (totals.s ?? 0) + (totals.r ?? 0);
      }
    }
  }
  const currentHour = new Date().getHours();
  return hours.map((value, h) => ({
    label: h % 6 === 0 ? `${String(h).padStart(2, "0")}:00` : "",
    value,
    emphasize: emphasizeCurrentHour && h === currentHour,
  }));
}

/**
 * Hour-of-day bars for a single day, one bar per day otherwise. Both come from
 * the same daily documents -- the hourly buckets are stored inside them, so
 * switching granularity costs no extra reads.
 */
function buildBars(docs: DailyDoc[], range: RangeKey, selectedDay?: string | null): Bar[] {
  if (selectedDay || range === "today") {
    return buildHourlyBars(docs, !selectedDay || selectedDay === recentDateKeys(1)[0]);
  }

  const byDate = new Map<string, number>();
  for (const d of docs) {
    byDate.set(d.date, (byDate.get(d.date) ?? 0) + d.bytesSent + d.bytesReceived);
  }

  const days = recentDateKeys(RANGE_DAYS[range]);
  const showEvery = days.length > 10 ? Math.ceil(days.length / 8) : 1;
  const today = days[days.length - 1];

  return days.map((date, i) => ({
    // "08-04" is enough context when the axis spans weeks
    label: i % showEvery === 0 ? date.slice(5) : "",
    value: byDate.get(date) ?? 0,
    emphasize: date === today,
  }));
}

export function UsageChart({ docs, range, selectedDay = null }: Props) {
  const bars = buildBars(docs, range, selectedDay);
  const max = Math.max(...bars.map((b) => b.value), 1);
  const hasData = bars.some((b) => b.value > 0);

  const width = 640;
  const height = 190;
  const padX = 6;
  const padTop = 18;
  const padBottom = 22;
  const plotHeight = height - padTop - padBottom;
  const slot = (width - padX * 2) / bars.length;
  const barWidth = Math.max(2, slot * 0.62);

  if (!hasData) {
    return (
      <div className="empty">
        No traffic recorded in this period yet.
        <br />
        Agents report every few minutes once installed.
      </div>
    );
  }

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         role="img" aria-label="Bandwidth over time">
      <text x={padX} y={11}>{formatBytes(max)} peak</text>

      {bars.map((bar, i) => {
        const barHeight = (bar.value / max) * plotHeight;
        const x = padX + i * slot + (slot - barWidth) / 2;
        const y = padTop + (plotHeight - barHeight);
        return (
          <g key={i}>
            {bar.value > 0 && (
              <rect
                className={bar.emphasize ? "bar" : "bar dim"}
                x={x}
                y={y}
                width={barWidth}
                height={Math.max(barHeight, 1.5)}
                rx={1.5}
              />
            )}
            {bar.label && (
              <text x={padX + i * slot + slot / 2} y={height - 6} textAnchor="middle">
                {bar.label}
              </text>
            )}
          </g>
        );
      })}

      <line className="axis" x1={padX} y1={padTop + plotHeight} x2={width - padX} y2={padTop + plotHeight} />
    </svg>
  );
}
