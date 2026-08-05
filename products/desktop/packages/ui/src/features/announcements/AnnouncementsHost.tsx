import { useBillingAnnouncementVisible } from "@posthog/ui/features/billing/useBillingAnnouncementVisible";
import { AnnouncementModal } from "./AnnouncementModal";
import { RequiredUpdateModal } from "./RequiredUpdateModal";
import { useActiveAnnouncement } from "./useActiveAnnouncement";

/**
 * Mounts the modal announcement surfaces (the banner mounts separately in the
 * shell). Defers to the billing announcement, which predates this system and
 * takes the stage alone.
 */
export function AnnouncementsHost() {
  const active = useActiveAnnouncement();
  const billingAnnouncementVisible = useBillingAnnouncementVisible();
  if (!active || billingAnnouncementVisible) return null;

  const { announcement } = active;
  if (announcement.kind === "required-update") {
    return <RequiredUpdateModal announcement={announcement} />;
  }
  if (announcement.style === "modal") {
    return (
      <AnnouncementModal
        announcement={announcement}
        needsUpdate={active.needsUpdate}
      />
    );
  }
  return null;
}
