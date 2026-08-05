import { z } from "zod";
import { isPostHogCodeDeeplink } from "./deep-links";

function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

const versionSchema = z.string().regex(/^\d+\.\d+\.\d+$/);

// https:// opens the default browser; posthog-code:// dispatches in-app.
const ctaUrlSchema = z
  .string()
  .refine((value) => isHttpsUrl(value) || isPostHogCodeDeeplink(value), {
    message: "must be an https:// URL or a posthog-code:// deep link",
  });

export const HERO_HEDGEHOGS = ["builder", "explorer", "happy", "loop"] as const;

/**
 * The modal's hero band. Absent = a default hedgehog; "none" = plain modal.
 * Banners never render a hero.
 */
const heroSchema = z.union([
  z.object({
    hedgehog: z.enum(HERO_HEDGEHOGS),
    /** Band background, hex only. Defaults to PostHog blue. */
    color: z
      .string()
      .regex(/^#[0-9a-fA-F]{6}$/)
      .optional(),
  }),
  z.object({ imageUrl: z.url({ protocol: /^https$/ }) }),
  z.object({ none: z.literal(true) }),
]);

const baseAnnouncementShape = {
  /** Stable per-user dismissal key. Changing it resurfaces the announcement. */
  id: z.string().min(1),
  title: z.string().min(1),
  /** Markdown. Modals render it in full; banners show only the first line. */
  body: z.string().min(1),
  startsAt: z.iso.datetime({ offset: true }).optional(),
  endsAt: z.iso.datetime({ offset: true }).optional(),
  hero: heroSchema.optional(),
};

export const announcementSchema = z
  .discriminatedUnion("kind", [
    z.object({
      ...baseAnnouncementShape,
      kind: z.literal("announcement"),
      style: z.enum(["banner", "modal"]).default("banner"),
      cta: z.object({ label: z.string().min(1), url: ctaUrlSchema }).optional(),
      /**
       * The announced feature needs at least this app version. Apps below it
       * show an "Update now" action in place of the cta; apps at or above it
       * show the cta.
       */
      minVersion: versionSchema.optional(),
      /**
       * Blocks until explicitly acknowledged: no dismiss, no Esc — only the
       * ack button, or the update action when the app is below minVersion
       * (updating counts as acknowledging). Modal style only.
       */
      requiresAck: z.boolean().default(false),
      /** Ack button label; the app defaults it to "OK". */
      ackLabel: z.string().min(1).optional(),
    }),
    z.object({
      ...baseAnnouncementShape,
      kind: z.literal("required-update"),
      /**
       * Rendered only when the running app is below this version — blocking and
       * non-dismissible. Users already at or above it never see anything.
       */
      minVersion: versionSchema,
    }),
  ])
  .superRefine((item, ctx) => {
    if (
      item.kind === "announcement" &&
      item.requiresAck &&
      item.style !== "modal"
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["style"],
        message: 'requiresAck announcements must use style: "modal"',
      });
    }
  });

/**
 * Loose envelope: items are validated one by one with announcementSchema so a
 * single malformed entry drops alone instead of killing the whole payload.
 */
export const announcementsEnvelopeSchema = z.object({
  announcements: z.array(z.unknown()),
});

/** Strict payload shape — what authoring tools validate before publishing. */
export const announcementsPayloadSchema = z.object({
  announcements: z.array(announcementSchema),
  /**
   * An on-stage announcement cancels the auto-opened What's New changelog.
   * Set false to defer it instead, showing both back to back.
   */
  suppressChangelog: z.boolean().default(true),
});

const changelogInterplaySchema = z.object({
  suppressChangelog: z.boolean().default(true),
});

/** Tolerant read of the payload's changelog policy — garbage means default. */
export function readSuppressChangelog(payload: unknown): boolean {
  const result = changelogInterplaySchema.safeParse(payload);
  return result.success ? result.data.suppressChangelog : true;
}

export type Announcement = z.infer<typeof announcementSchema>;
export type AnnouncementHero = z.infer<typeof heroSchema>;
export type AnnouncementsPayload = z.infer<typeof announcementsPayloadSchema>;
