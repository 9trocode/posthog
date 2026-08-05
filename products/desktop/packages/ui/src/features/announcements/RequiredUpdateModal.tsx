import { Dialog, DialogContent, DialogTitle } from "@posthog/quill";
import { ANALYTICS_EVENTS } from "@posthog/shared/analytics-events";
import type { Announcement } from "@posthog/shared/announcements";
import { MarkdownRenderer } from "@posthog/ui/features/editor/components/MarkdownRenderer";
import { track } from "@posthog/ui/shell/analytics";
import { useEffect } from "react";
import { UpdateAction } from "./UpdateAction";

type RequiredUpdate = Extract<Announcement, { kind: "required-update" }>;

/**
 * Blocking: stays open until the user updates. Controlled `open` with no
 * onOpenChange means Esc and outside clicks change nothing, and the close
 * button is suppressed — the only way forward is the update action.
 */
export function RequiredUpdateModal({
  announcement,
}: {
  announcement: RequiredUpdate;
}) {
  useEffect(() => {
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_SHOWN, {
      announcement_id: announcement.id,
      announcement_kind: announcement.kind,
      announcement_style: "modal",
    });
  }, [announcement.id, announcement.kind]);

  return (
    <Dialog open>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <div className="flex flex-col gap-4 px-5 pt-5 pb-5">
          <div className="flex flex-col gap-1.5">
            <DialogTitle className="font-semibold text-[17px] text-gray-12 tracking-tight">
              {announcement.title}
            </DialogTitle>
            <div className="text-[13px] text-gray-11 leading-relaxed">
              <MarkdownRenderer content={announcement.body} />
            </div>
          </div>
          <div className="flex justify-end pt-1">
            <UpdateAction
              analytics={{
                announcement_id: announcement.id,
                announcement_kind: announcement.kind,
                announcement_style: "modal",
              }}
              showProgress
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
