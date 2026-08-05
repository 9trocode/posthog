import { announcementsPayloadSchema } from "@posthog/shared/announcements";
import { useMemo, useState } from "react";
import { z } from "zod";
import { type FlagRecord, readPayload, savePayload } from "./api";
import { POSTHOG_HOST, PROJECT_ID } from "./config";
import {
  blankItem,
  type EditableItem,
  isoToLocalInput,
  localInputToIso,
  toEditable,
  toPayloadItem,
} from "./items";
import { Preview } from "./Preview";

function rolloutLabel(flag: FlagRecord): { text: string; live: boolean } {
  if (!flag.active) return { text: "flag disabled", live: false };
  const groups = flag.filters.groups as
    | { rollout_percentage?: number | null }[]
    | undefined;
  const percent = Math.max(
    0,
    ...(groups ?? []).map((g) => g.rollout_percentage ?? 100),
  );
  return percent > 0
    ? { text: `${percent}% on air`, live: true }
    : { text: "0% · dark", live: false };
}

export function Editor({
  token,
  flag,
  onFlagUpdated,
  onLogout,
}: {
  token: string;
  flag: FlagRecord;
  onFlagUpdated: (flag: FlagRecord) => void;
  onLogout: () => void;
}) {
  const initial = useMemo(() => {
    const parsed = announcementsPayloadSchema.safeParse(readPayload(flag));
    return parsed.success ? toEditable(parsed.data.announcements) : null;
  }, [flag]);

  const [items, setItems] = useState<EditableItem[]>(initial ?? []);
  const [selected, setSelected] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [published, setPublished] = useState(false);
  const [jsonDraft, setJsonDraft] = useState<string | null>(null);

  const flagUrl = `${POSTHOG_HOST}/project/${PROJECT_ID}/feature_flags/${flag.id}`;
  const rollout = rolloutLabel(flag);
  const selectedItem = items[Math.min(selected, items.length - 1)] ?? null;

  const payloadJson = useMemo(
    () => JSON.stringify({ announcements: items.map(toPayloadItem) }, null, 2),
    [items],
  );

  const update = (index: number, patch: Partial<EditableItem>) => {
    setItems((prev) =>
      prev.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
    setPublished(false);
  };

  const move = (index: number, delta: number) => {
    setItems((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setSelected(Math.max(0, Math.min(index + delta, items.length - 1)));
    setPublished(false);
  };

  const remove = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
    setSelected((prev) => Math.max(0, prev > index ? prev - 1 : prev));
    setPublished(false);
  };

  const add = (kind: EditableItem["kind"]) => {
    setItems((prev) => [...prev, blankItem(kind)]);
    setSelected(items.length);
    setPublished(false);
  };

  const applyJson = () => {
    if (jsonDraft === null) return;
    try {
      const parsed = announcementsPayloadSchema.parse(JSON.parse(jsonDraft));
      setItems(toEditable(parsed.announcements));
      setJsonDraft(null);
      setErrors([]);
    } catch (error) {
      setErrors(
        error instanceof z.ZodError
          ? error.issues.map((i) => `${i.path.join(".")}: ${i.message}`)
          : [String(error)],
      );
    }
  };

  const publish = async () => {
    const parsed = announcementsPayloadSchema.safeParse({
      announcements: items.map(toPayloadItem),
    });
    if (!parsed.success) {
      setErrors(
        parsed.error.issues.map(
          (i) => `announcements.${i.path.join(".")}: ${i.message}`,
        ),
      );
      return;
    }
    setErrors([]);
    setSaving(true);
    try {
      onFlagUpdated(await savePayload(token, flag, parsed.data));
      setPublished(true);
    } catch (error) {
      setErrors([String(error)]);
    } finally {
      setSaving(false);
    }
  };

  if (initial === null) {
    return (
      <div className="console">
        <p className="errors">
          The current flag payload does not match the announcements schema. Fix
          it on{" "}
          <a href={flagUrl} target="_blank" rel="noreferrer">
            the flag
          </a>{" "}
          and reload.
        </p>
      </div>
    );
  }

  return (
    <div className="console">
      <header className="masthead">
        <div>
          <span className="eyebrow">PostHog Desktop · internal</span>
          <h1>Announcements</h1>
        </div>
        <div className="masthead-status">
          <a
            className="flag-chip"
            href={flagUrl}
            target="_blank"
            rel="noreferrer"
          >
            posthog-desktop-announcements
          </a>
          <span className={rollout.live ? "pill pill-live" : "pill"}>
            <span className="pill-dot" aria-hidden />
            {rollout.text}
          </span>
          <button type="button" className="btn btn-ghost" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>

      <div className="cols">
        <main className="queue">
          <p className="queue-note">
            Order is priority — the app shows the first eligible item only.
          </p>

          {items.map((item, index) => (
            <section
              className={index === selected ? "card card-selected" : "card"}
              key={`${index}-${item.kind}`}
              onFocusCapture={() => setSelected(index)}
              onPointerDown={() => setSelected(index)}
            >
              <div className="card-head">
                <span className="card-index">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span
                  className={
                    item.kind === "required-update"
                      ? "kind-tag kind-update"
                      : "kind-tag"
                  }
                >
                  {item.kind === "required-update"
                    ? "Required update"
                    : "Announcement"}
                </span>
                <span className="spacer" />
                <button
                  type="button"
                  className="btn btn-icon"
                  aria-label="Move up"
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="btn btn-icon"
                  aria-label="Move down"
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => remove(index)}
                >
                  Remove
                </button>
              </div>

              <div className="grid">
                <label>
                  id — dismissal key
                  <input
                    className="mono"
                    placeholder="loops-launch"
                    value={item.id}
                    onChange={(e) => update(index, { id: e.target.value })}
                  />
                </label>
                <label>
                  title
                  <input
                    placeholder="Introducing…"
                    value={item.title}
                    onChange={(e) => update(index, { title: e.target.value })}
                  />
                </label>
              </div>

              <label>
                body — markdown; banners show the first line
                <textarea
                  rows={3}
                  value={item.body}
                  onChange={(e) => update(index, { body: e.target.value })}
                />
              </label>

              <div className="grid">
                <label>
                  starts — optional
                  <input
                    type="datetime-local"
                    className="mono"
                    value={isoToLocalInput(item.startsAt)}
                    onChange={(e) =>
                      update(index, {
                        startsAt: localInputToIso(e.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  ends — optional
                  <input
                    type="datetime-local"
                    className="mono"
                    value={isoToLocalInput(item.endsAt)}
                    onChange={(e) =>
                      update(index, { endsAt: localInputToIso(e.target.value) })
                    }
                  />
                </label>
                <label>
                  min version{" "}
                  {item.kind === "required-update"
                    ? "— blocks older apps"
                    : "— optional update nudge"}
                  <input
                    className="mono"
                    placeholder="1.42.0"
                    value={item.minVersion}
                    onChange={(e) =>
                      update(index, { minVersion: e.target.value })
                    }
                  />
                </label>
                {item.kind === "announcement" && (
                  <label>
                    style
                    <select
                      value={item.style}
                      onChange={(e) =>
                        update(
                          index,
                          e.target.value === "banner"
                            ? { style: "banner", requiresAck: false }
                            : { style: "modal" },
                        )
                      }
                    >
                      <option value="banner">banner</option>
                      <option value="modal">modal</option>
                    </select>
                  </label>
                )}
              </div>

              {item.kind === "announcement" && item.style === "modal" && (
                <div className="grid">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={item.requiresAck}
                      onChange={(e) =>
                        update(index, { requiresAck: e.target.checked })
                      }
                    />
                    require acknowledgement — blocks until confirmed; updating
                    counts
                  </label>
                  {item.requiresAck && (
                    <label>
                      ack button label
                      <input
                        placeholder="OK"
                        value={item.ackLabel}
                        onChange={(e) =>
                          update(index, { ackLabel: e.target.value })
                        }
                      />
                    </label>
                  )}
                </div>
              )}

              {item.kind === "announcement" && !item.requiresAck && (
                <div className="grid">
                  <label>
                    {item.minVersion
                      ? "button label — shown once the app is up to date"
                      : "button label — optional"}
                    <input
                      placeholder="Learn more"
                      value={item.ctaLabel}
                      onChange={(e) =>
                        update(index, { ctaLabel: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    button link — https:// or posthog-code://
                    <input
                      className="mono"
                      placeholder="posthog-code://loop"
                      value={item.ctaUrl}
                      onChange={(e) =>
                        update(index, { ctaUrl: e.target.value })
                      }
                    />
                  </label>
                </div>
              )}
            </section>
          ))}

          <div className="row">
            <button
              type="button"
              className="btn"
              onClick={() => add("announcement")}
            >
              + Announcement
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => add("required-update")}
            >
              + Required update
            </button>
          </div>

          <details className="json">
            <summary>Raw JSON</summary>
            <textarea
              rows={14}
              className="mono"
              value={jsonDraft ?? payloadJson}
              onChange={(e) => setJsonDraft(e.target.value)}
            />
            <button
              type="button"
              className="btn"
              disabled={jsonDraft === null}
              onClick={applyJson}
            >
              Apply JSON
            </button>
          </details>

          {errors.length > 0 && (
            <ul className="errors">
              {errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}

          <div className="row publish-row">
            <button
              type="button"
              className="btn btn-publish"
              disabled={saving}
              onClick={() => void publish()}
            >
              {saving ? "Publishing…" : "Publish"}
            </button>
            <span className="publish-note">
              {published
                ? "Published — live wherever the flag is rolled out."
                : "Writes the flag payload. Rollout % is unchanged."}
            </span>
          </div>
        </main>

        <aside className="side">
          <span className="eyebrow">In-app preview</span>
          <Preview item={selectedItem} />
        </aside>
      </div>
    </div>
  );
}
