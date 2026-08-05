import { Button, Dialog, DialogContent, DialogTitle } from "@posthog/quill";
import {
  ANALYTICS_EVENTS,
  type AnnouncementProperties,
} from "@posthog/shared/analytics-events";
import type { Announcement } from "@posthog/shared/announcements";
import { MarkdownRenderer } from "@posthog/ui/features/editor/components/MarkdownRenderer";
import { track } from "@posthog/ui/shell/analytics";
import { useEffect } from "react";
import { AnnouncementHero } from "./AnnouncementHero";
import { openAnnouncementCta } from "./announcementCta";
import { useAnnouncementsStore } from "./announcementsStore";
import { UpdateAction } from "./UpdateAction";

type ModalAnnouncement = Extract<Announcement, { kind: "announcement" }>;

export function AnnouncementModal({
  announcement,
  needsUpdate,
}: {
  announcement: ModalAnnouncement;
  needsUpdate: boolean;
}) {
  const dismiss = useAnnouncementsStore((state) => state.dismiss);
  const analytics: AnnouncementProperties = {
    announcement_id: announcement.id,
    announcement_kind: announcement.kind,
    announcement_style: "modal",
  };

  useEffect(() => {
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_SHOWN, {
      announcement_id: announcement.id,
      announcement_kind: announcement.kind,
      announcement_style: "modal",
    });
  }, [announcement.id, announcement.kind]);

  const handleClose = () => {
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_DISMISSED, analytics);
    dismiss(announcement.id);
  };

  // Engaging with the CTA retires the announcement the same way closing does.
  const handleCta = (url: string) => {
    const ctaType = openAnnouncementCta(url);
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_CTA_CLICKED, {
      ...analytics,
      cta_type: ctaType,
    });
    dismiss(announcement.id);
  };

  const acknowledge = (ackType: "ok" | "update") => {
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_ACKNOWLEDGED, {
      ...analytics,
      ack_type: ackType,
    });
    dismiss(announcement.id);
  };

  const blocking = announcement.requiresAck;

  return (
    <Dialog
      open
      onOpenChange={
        blocking
          ? undefined
          : (open) => {
              if (!open) handleClose();
            }
      }
    >
      <DialogContent className="sm:max-w-md" showCloseButton={!blocking}>
        <AnnouncementHero hero={announcement.hero} defaultHedgehog="happy" />
        <div className="flex flex-col gap-4 px-5 pt-4 pb-5">
          <div className="flex flex-col gap-1.5">
            <DialogTitle className="font-semibold text-[17px] text-gray-12 tracking-tight">
              {announcement.title}
            </DialogTitle>
            <div className="text-[13px] text-gray-11 leading-relaxed">
              <MarkdownRenderer content={announcement.body} />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            {blocking ? (
              needsUpdate ? (
                <UpdateAction
                  analytics={analytics}
                  showProgress
                  onActivated={() => acknowledge("update")}
                />
              ) : (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => acknowledge("ok")}
                >
                  {announcement.ackLabel ?? "OK"}
                </Button>
              )
            ) : (
              <>
                <Button variant="outline" size="sm" onClick={handleClose}>
                  Dismiss
                </Button>
                {needsUpdate ? (
                  <UpdateAction analytics={analytics} showProgress />
                ) : announcement.cta ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() =>
                      announcement.cta && handleCta(announcement.cta.url)
                    }
                  >
                    {announcement.cta.label}
                  </Button>
                ) : null}
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
