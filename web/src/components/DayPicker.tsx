import { formatDayLabel } from "../lib/format";

interface Props {
  days: string[];
  selectedDay: string | null;
  onSelect: (dateKey: string) => void;
}

/**
 * Pick one retained day (up to 30) to inspect. Newest first so "Today" is
 * the first chip; keyboard users can also jump via the native select.
 */
export function DayPicker({ days, selectedDay, onSelect }: Props) {
  const newestFirst = [...days].reverse();

  return (
    <section className="day-picker" aria-label="Select a day to view usage">
      <div className="day-picker-head">
        <div>
          <h2>Day usage</h2>
          <p>Choose any of the last {days.length} days to inspect bandwidth and sites.</p>
        </div>
        <label className="day-select-label">
          <span className="sr-only">Jump to day</span>
          <select
            value={selectedDay ?? ""}
            aria-label="Jump to day"
            onChange={(e) => {
              const value = e.target.value;
              if (value) onSelect(value);
            }}
          >
            {!selectedDay && (
              <option value="" disabled>
                Viewing a date range
              </option>
            )}
            {newestFirst.map((key) => (
              <option key={key} value={key}>
                {formatDayLabel(key)} ({key})
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="day-chip-row" role="listbox" aria-label="Days">
        {newestFirst.map((key) => {
          const isSelected = selectedDay === key;
          return (
            <button
              key={key}
              type="button"
              role="option"
              aria-selected={isSelected}
              className={`day-chip${isSelected ? " is-selected" : ""}`}
              onClick={() => onSelect(key)}
            >
              <span className="day-chip-label">{formatDayLabel(key)}</span>
              <span className="day-chip-date">{key.slice(5)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
