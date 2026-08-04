interface Props {
  deviceName: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDeleteModal({
  deviceName,
  busy,
  error,
  onCancel,
  onConfirm,
}: Props) {
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={() => {
        if (!busy) onCancel();
      }}
    >
      <div
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-device-title"
        aria-describedby="delete-device-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="delete-device-title">Remove this PC?</h2>
        <p id="delete-device-desc">
          This permanently deletes <strong>{deviceName}</strong> and all of its
          usage history (sites, apps, bandwidth) from the dashboard. The agent
          on that PC will stop being accepted until it is reinstalled with the
          enrollment token.
        </p>
        <p className="modal-warn">This cannot be undone.</p>
        {error && <p className="modal-error">{error}</p>}
        <div className="modal-actions">
          <button
            type="button"
            className="ghost-btn"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            className="danger-btn"
            onClick={onConfirm}
            disabled={busy}
            aria-busy={busy}
          >
            {busy ? "Removing…" : "Remove PC"}
          </button>
        </div>
      </div>
    </div>
  );
}
