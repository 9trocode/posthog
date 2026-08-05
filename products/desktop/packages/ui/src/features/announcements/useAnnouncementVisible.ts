import { useActiveAnnouncement } from "./useActiveAnnouncement";

/** Any remote announcement is on stage — lower-priority surfaces defer. */
export function useAnnouncementVisible(): boolean {
  return useActiveAnnouncement() !== null;
}

/** A blocking required-update announcement is on stage. */
export function useBlockingAnnouncementVisible(): boolean {
  const active = useActiveAnnouncement();
  return active !== null && active.announcement.kind === "required-update";
}
