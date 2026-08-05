import {
  electronStorage,
  flushRendererStateWrites,
} from "@posthog/ui/shell/rendererStorage";
import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AnnouncementsState {
  dismissedIds: Record<string, true>;
  // Hydration is async (Electron storage over IPC); announcements must not
  // flash for users whose persisted dismissals haven't been read back yet.
  _hasHydrated: boolean;
  dismiss: (id: string) => void;
  setHasHydrated: (hydrated: boolean) => void;
}

export const useAnnouncementsStore = create<AnnouncementsState>()(
  persist(
    (set) => ({
      dismissedIds: {},
      _hasHydrated: false,
      // Flushed immediately: the debounced write could otherwise be lost if
      // the window closes right after the click, resurrecting the announcement.
      dismiss: (id) => {
        set((state) => ({
          dismissedIds: { ...state.dismissedIds, [id]: true },
        }));
        void flushRendererStateWrites();
      },
      setHasHydrated: (hydrated) => set({ _hasHydrated: hydrated }),
    }),
    {
      name: "posthog-desktop-announcements-dismissed",
      storage: electronStorage,
      partialize: (state) => ({ dismissedIds: state.dismissedIds }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.setHasHydrated(true);
          return;
        }
        useAnnouncementsStore.setState({ _hasHydrated: true });
      },
    },
  ),
);
