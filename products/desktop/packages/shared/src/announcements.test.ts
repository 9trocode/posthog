import { describe, expect, it } from "vitest";
import {
  announcementSchema,
  announcementsEnvelopeSchema,
  announcementsPayloadSchema,
  readSuppressChangelog,
} from "./announcements";

const validAnnouncement = {
  kind: "announcement",
  id: "loops-launch",
  title: "Introducing Loops",
  body: "Recurring agent jobs are here.",
};

const validRequiredUpdate = {
  kind: "required-update",
  id: "breaking-2026-08",
  title: "Update required",
  body: "This version can no longer talk to the backend.",
  minVersion: "1.42.0",
};

describe("announcements schema", () => {
  it.each([
    ["minimal announcement", validAnnouncement],
    ["announcement with modal style", { ...validAnnouncement, style: "modal" }],
    [
      "announcement with https cta",
      {
        ...validAnnouncement,
        cta: { label: "Learn more", url: "https://posthog.com/code" },
      },
    ],
    [
      "announcement with deep-link cta",
      {
        ...validAnnouncement,
        cta: { label: "Open loops", url: "posthog-code://loop/abc" },
      },
    ],
    [
      "announcement with dev deep-link cta",
      {
        ...validAnnouncement,
        cta: { label: "Open", url: "posthog-code-dev://inbox/123" },
      },
    ],
    [
      "announcement with schedule",
      {
        ...validAnnouncement,
        startsAt: "2026-08-05T10:00:00Z",
        endsAt: "2026-08-12T10:00:00+02:00",
      },
    ],
    [
      "announcement with minVersion nudge",
      { ...validAnnouncement, minVersion: "1.40.0" },
    ],
    [
      "requiresAck modal",
      {
        ...validAnnouncement,
        style: "modal",
        requiresAck: true,
        ackLabel: "I understand",
      },
    ],
    ["required update", validRequiredUpdate],
  ])("accepts %s", (_name, input) => {
    expect(announcementSchema.safeParse(input).success).toBe(true);
  });

  it("defaults announcement style to banner", () => {
    const parsed = announcementSchema.parse(validAnnouncement);
    expect(parsed.kind === "announcement" && parsed.style).toBe("banner");
  });

  it.each([
    ["unknown kind", { ...validAnnouncement, kind: "promo" }],
    ["missing id", { ...validAnnouncement, id: undefined }],
    ["empty id", { ...validAnnouncement, id: "" }],
    ["missing title", { ...validAnnouncement, title: undefined }],
    ["missing body", { ...validAnnouncement, body: undefined }],
    [
      "http cta url",
      { ...validAnnouncement, cta: { label: "Go", url: "http://x.com" } },
    ],
    [
      "relative cta url",
      { ...validAnnouncement, cta: { label: "Go", url: "/settings" } },
    ],
    ["non-ISO startsAt", { ...validAnnouncement, startsAt: "tomorrow" }],
    ["date-only startsAt", { ...validAnnouncement, startsAt: "2026-08-05" }],
    ["two-part minVersion", { ...validAnnouncement, minVersion: "1.42" }],
    ["prefixed minVersion", { ...validAnnouncement, minVersion: "v1.42.0" }],
    [
      "required update without minVersion",
      { ...validRequiredUpdate, minVersion: undefined },
    ],
    [
      "required update with bad minVersion",
      { ...validRequiredUpdate, minVersion: "latest" },
    ],
    [
      "requiresAck banner (implicit style)",
      { ...validAnnouncement, requiresAck: true },
    ],
    [
      "requiresAck banner (explicit style)",
      { ...validAnnouncement, style: "banner", requiresAck: true },
    ],
    [
      "empty ackLabel",
      { ...validAnnouncement, style: "modal", requiresAck: true, ackLabel: "" },
    ],
  ])("rejects %s", (_name, input) => {
    expect(announcementSchema.safeParse(input).success).toBe(false);
  });

  it("envelope accepts malformed items for per-item validation", () => {
    const result = announcementsEnvelopeSchema.safeParse({
      announcements: [validAnnouncement, { garbage: true }],
    });
    expect(result.success).toBe(true);
  });

  it("strict payload rejects a single malformed item", () => {
    const result = announcementsPayloadSchema.safeParse({
      announcements: [validAnnouncement, { garbage: true }],
    });
    expect(result.success).toBe(false);
  });

  it.each([
    ["absent field", { announcements: [] }, true],
    ["explicit false", { announcements: [], suppressChangelog: false }, false],
    ["non-object payload", "garbage", true],
    ["non-boolean value", { suppressChangelog: "yes" }, true],
  ])("readSuppressChangelog: %s → %s", (_name, payload, expected) => {
    expect(readSuppressChangelog(payload)).toBe(expected);
  });
});
