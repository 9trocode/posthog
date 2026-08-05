import type { Announcement } from "@posthog/shared/announcements";
import { announcementsPayloadSchema } from "@posthog/shared/announcements";
import { useMemo, useState } from "react";
import { z } from "zod";
import { type FlagRecord, readPayload, savePayload } from "./api";
import { POSTHOG_HOST, PROJECT_ID } from "./config";

interface EditableItem {
  kind: "announcement" | "required-update";
  id: string;
  title: string;
  body: string;
  startsAt: string;
  endsAt: string;
  style: "banner" | "modal";
  minVersion: string;
  ctaLabel: string;
  ctaUrl: string;
}

function blankItem(kind: EditableItem["kind"]): EditableItem {
  return {
    kind,
    id: "",
    title: "",
    body: "",
    startsAt: "",
    endsAt: "",
    style: "banner",
    minVersion: "",
    ctaLabel: "",
    ctaUrl: "",
  };
}

function toEditable(items: Announcement[]): EditableItem[] {
  return items.map((item) => ({
    ...blankItem(item.kind),
    id: item.id,
    title: item.title,
    body: item.body,
    startsAt: item.startsAt ?? "",
    endsAt: item.endsAt ?? "",
    style: item.kind === "announcement" ? item.style : "banner",
    minVersion: item.minVersion ?? "",
    ctaLabel: item.kind === "announcement" ? (item.cta?.label ?? "") : "",
    ctaUrl: item.kind === "announcement" ? (item.cta?.url ?? "") : "",
  }));
}

function toPayloadItem(item: EditableItem): Record<string, unknown> {
  const base: Record<string, unknown> = {
    kind: item.kind,
    id: item.id,
    title: item.title,
    body: item.body,
  };
  if (item.startsAt) base.startsAt = item.startsAt;
  if (item.endsAt) base.endsAt = item.endsAt;
  if (item.kind === "required-update") {
    base.minVersion = item.minVersion;
    return base;
  }
  base.style = item.style;
  if (item.minVersion) base.minVersion = item.minVersion;
  if (item.ctaLabel || item.ctaUrl) {
    base.cta = { label: item.ctaLabel, url: item.ctaUrl };
  }
  return base;
}

function isoToLocalInput(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function localInputToIso(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
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
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [jsonDraft, setJsonDraft] = useState<string | null>(null);

  const payloadJson = useMemo(
    () => JSON.stringify({ announcements: items.map(toPayloadItem) }, null, 2),
    [items],
  );

  const update = (index: number, patch: Partial<EditableItem>) => {
    setItems((prev) =>
      prev.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
    setSavedAt(null);
  };

  const move = (index: number, delta: number) => {
    setItems((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setSavedAt(null);
  };

  const remove = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
    setSavedAt(null);
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

  const save = async () => {
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
      setSavedAt(Date.now());
    } catch (error) {
      setErrors([String(error)]);
    } finally {
      setSaving(false);
    }
  };

  if (initial === null) {
    return (
      <div className="panel">
        <p className="error">
          The current flag payload does not match the announcements schema. Fix
          it in{" "}
          <a
            href={`${POSTHOG_HOST}/project/${PROJECT_ID}/feature_flags/${flag.id}`}
            target="_blank"
            rel="noreferrer"
          >
            PostHog
          </a>{" "}
          and reload.
        </p>
      </div>
    );
  }

  return (
    <div className="editor">
      <header>
        <div>
          <h1>PostHog Desktop announcements</h1>
          <p className="muted">
            Order is priority — the first eligible item shows. Rollout stays
            managed on{" "}
            <a
              href={`${POSTHOG_HOST}/project/${PROJECT_ID}/feature_flags/${flag.id}`}
              target="_blank"
              rel="noreferrer"
            >
              the flag
            </a>
            {flag.active ? "" : " (currently disabled)"}.
          </p>
        </div>
        <button type="button" className="ghost" onClick={onLogout}>
          Log out
        </button>
      </header>

      {items.map((item, index) => (
        <section className="panel" key={`${index}-${item.kind}`}>
          <div className="panel-head">
            <strong>
              {item.kind === "required-update"
                ? "Required update"
                : "Announcement"}
            </strong>
            <span className="spacer" />
            <button type="button" onClick={() => move(index, -1)}>
              ↑
            </button>
            <button type="button" onClick={() => move(index, 1)}>
              ↓
            </button>
            <button type="button" onClick={() => remove(index)}>
              Remove
            </button>
          </div>
          <div className="grid">
            <label>
              id (dismissal key)
              <input
                value={item.id}
                onChange={(e) => update(index, { id: e.target.value })}
              />
            </label>
            <label>
              title
              <input
                value={item.title}
                onChange={(e) => update(index, { title: e.target.value })}
              />
            </label>
          </div>
          <label>
            body (markdown; banners show the first line)
            <textarea
              rows={3}
              value={item.body}
              onChange={(e) => update(index, { body: e.target.value })}
            />
          </label>
          <div className="grid">
            <label>
              starts at (optional)
              <input
                type="datetime-local"
                value={isoToLocalInput(item.startsAt)}
                onChange={(e) =>
                  update(index, { startsAt: localInputToIso(e.target.value) })
                }
              />
            </label>
            <label>
              ends at (optional)
              <input
                type="datetime-local"
                value={isoToLocalInput(item.endsAt)}
                onChange={(e) =>
                  update(index, { endsAt: localInputToIso(e.target.value) })
                }
              />
            </label>
            <label>
              min version{" "}
              {item.kind === "required-update"
                ? "(required — apps below it are blocked)"
                : "(optional — stale apps get an Update button)"}
              <input
                placeholder="1.42.0"
                value={item.minVersion}
                onChange={(e) => update(index, { minVersion: e.target.value })}
              />
            </label>
            {item.kind === "announcement" && (
              <label>
                style
                <select
                  value={item.style}
                  onChange={(e) =>
                    update(index, {
                      style: e.target.value as EditableItem["style"],
                    })
                  }
                >
                  <option value="banner">banner</option>
                  <option value="modal">modal</option>
                </select>
              </label>
            )}
          </div>
          {item.kind === "announcement" && (
            <div className="grid">
              <label>
                cta label (optional)
                <input
                  value={item.ctaLabel}
                  onChange={(e) => update(index, { ctaLabel: e.target.value })}
                />
              </label>
              <label>
                cta url (https://… or posthog-code://…)
                <input
                  value={item.ctaUrl}
                  onChange={(e) => update(index, { ctaUrl: e.target.value })}
                />
              </label>
            </div>
          )}
        </section>
      ))}

      <div className="row">
        <button
          type="button"
          onClick={() =>
            setItems((prev) => [...prev, blankItem("announcement")])
          }
        >
          Add announcement
        </button>
        <button
          type="button"
          onClick={() =>
            setItems((prev) => [...prev, blankItem("required-update")])
          }
        >
          Add required update
        </button>
      </div>

      <details>
        <summary>Raw JSON</summary>
        <textarea
          rows={14}
          value={jsonDraft ?? payloadJson}
          onChange={(e) => setJsonDraft(e.target.value)}
        />
        <button type="button" disabled={jsonDraft === null} onClick={applyJson}>
          Apply JSON
        </button>
      </details>

      {errors.length > 0 && (
        <ul className="error">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}

      <div className="row">
        <button
          type="button"
          className="primary"
          disabled={saving}
          onClick={() => void save()}
        >
          {saving ? "Publishing…" : "Publish payload"}
        </button>
        {savedAt !== null && <span className="muted">Published ✓</span>}
      </div>
    </div>
  );
}
