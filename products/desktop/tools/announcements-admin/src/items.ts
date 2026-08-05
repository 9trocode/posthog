import type { Announcement } from "@posthog/shared/announcements";

export interface EditableItem {
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

export function blankItem(kind: EditableItem["kind"]): EditableItem {
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

export function toEditable(items: Announcement[]): EditableItem[] {
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

export function toPayloadItem(item: EditableItem): Record<string, unknown> {
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

export function isoToLocalInput(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function localInputToIso(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}
