import type { EditableItem } from "./items";

function firstLine(body: string): string {
  return body.split("\n")[0] || "Body text appears here";
}

function BannerMock({ item }: { item: EditableItem }) {
  return (
    <div className="pv-banner">
      <span className="pv-banner-icon" aria-hidden>
        📣
      </span>
      <span className="pv-banner-text">
        <strong>{item.title || "Announcement title"}</strong>
        <em>{firstLine(item.body)}</em>
      </span>
      {item.minVersion ? (
        <span className="pv-btn pv-btn-solid">Update now</span>
      ) : item.ctaLabel ? (
        <span className="pv-btn">{item.ctaLabel}</span>
      ) : null}
      <span className="pv-x" aria-hidden>
        ✕
      </span>
    </div>
  );
}

function ModalMock({ item }: { item: EditableItem }) {
  const blocking = item.kind === "required-update" || item.requiresAck;
  const primary =
    item.kind === "required-update"
      ? "Restart to update"
      : item.minVersion
        ? "Update now"
        : item.requiresAck
          ? item.ackLabel || "OK"
          : item.ctaLabel || null;
  return (
    <div className="pv-scrim">
      <div className="pv-modal">
        <strong>{item.title || "Announcement title"}</strong>
        <p>{item.body || "Body text appears here. Markdown renders in-app."}</p>
        <div className="pv-modal-actions">
          {!blocking && <span className="pv-btn">Dismiss</span>}
          {primary && <span className="pv-btn pv-btn-solid">{primary}</span>}
        </div>
      </div>
    </div>
  );
}

/** The announcement as PostHog Desktop will render it, inside a mock window. */
export function Preview({ item }: { item: EditableItem | null }) {
  const isBanner =
    item?.kind === "announcement" &&
    item.style === "banner" &&
    !item.requiresAck;
  return (
    <div className="preview">
      <div className="pv-frame" aria-label="Preview of the desktop app">
        <div className="pv-titlebar" aria-hidden>
          <span className="pv-dot" />
          <span className="pv-dot" />
          <span className="pv-dot" />
          <span className="pv-tab" />
        </div>
        {item && isBanner && <BannerMock item={item} />}
        <div className="pv-body">
          <div className="pv-sidebar" aria-hidden>
            <span />
            <span />
            <span />
          </div>
          <div className="pv-content" aria-hidden>
            <span />
            <span />
            <span />
            <span />
          </div>
          {item && !isBanner && <ModalMock item={item} />}
        </div>
      </div>
      {item ? (
        <ul className="pv-facts">
          {item.kind === "required-update" ? (
            <li>
              Blocks every app below{" "}
              <code>{item.minVersion || "minVersion"}</code> until it updates.
              Up-to-date users see nothing. Cannot be dismissed.
            </li>
          ) : (
            <>
              {item.requiresAck ? (
                <li>
                  Blocks until acknowledged — updating counts as
                  acknowledgement. Keyed on <code>{item.id || "id"}</code>.
                </li>
              ) : (
                <li>
                  Dismissible. Dismissal is keyed on{" "}
                  <code>{item.id || "id"}</code> — change the id to resurface
                  it.
                </li>
              )}
              {item.minVersion && (
                <li>
                  Apps below <code>{item.minVersion}</code> get "Update now"
                  instead of the {item.requiresAck ? "ack button" : "CTA"}.
                </li>
              )}
            </>
          )}
          {(item.startsAt || item.endsAt) && (
            <li>
              Shows {item.startsAt && `from ${item.startsAt}`}
              {item.startsAt && item.endsAt && " "}
              {item.endsAt && `until ${item.endsAt}`}.
            </li>
          )}
        </ul>
      ) : (
        <p className="pv-empty">Add an announcement to preview it.</p>
      )}
    </div>
  );
}
