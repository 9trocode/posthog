import { useHostTRPC } from "@posthog/host-router/react";
import { Button, Progress } from "@posthog/quill";
import {
  ANALYTICS_EVENTS,
  type AnnouncementProperties,
} from "@posthog/shared/analytics-events";
import {
  useInstallUpdate,
  useUpdateView,
} from "@posthog/ui/features/updates/updateStore";
import { track } from "@posthog/ui/shell/analytics";
import { openExternalUrl } from "@posthog/ui/shell/openExternal";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

const MANUAL_DOWNLOAD_URL = "https://github.com/PostHog/code/releases/latest";

/**
 * The announcement-side entry into the existing update flow: kicks a check,
 * then walks available → downloading → restart. Where the updater is
 * unavailable (Linux, dev builds), degrades to a manual-download link.
 */
export function UpdateAction({
  analytics,
  showProgress = false,
}: {
  analytics: AnnouncementProperties;
  showProgress?: boolean;
}) {
  const { status, isEnabled, downloadPercent } = useUpdateView();
  const installUpdate = useInstallUpdate();
  const hostTRPC = useHostTRPC();
  const { mutate: runCheck, isPending: isCheckPending } = useMutation(
    hostTRPC.updates.check.mutationOptions(),
  );
  const { mutate: runDownload, isPending: isDownloadPending } = useMutation(
    hostTRPC.updates.download.mutationOptions(),
  );

  // This surface exists because an update is wanted, so make the state
  // actionable immediately instead of waiting for the hourly poll.
  const kicked = useRef(false);
  useEffect(() => {
    if (!isEnabled || kicked.current || status !== "idle") return;
    kicked.current = true;
    runCheck(undefined);
  }, [isEnabled, status, runCheck]);

  const trackClick = () => {
    track(ANALYTICS_EVENTS.ANNOUNCEMENT_CTA_CLICKED, {
      ...analytics,
      cta_type: "update",
    });
  };

  if (!isEnabled) {
    return (
      <Button
        variant="primary"
        size="sm"
        onClick={() => {
          trackClick();
          openExternalUrl(MANUAL_DOWNLOAD_URL);
        }}
      >
        Download the latest version
      </Button>
    );
  }

  if (status === "ready" || status === "installing") {
    return (
      <Button
        variant="primary"
        size="sm"
        disabled={status === "installing"}
        onClick={() => {
          trackClick();
          void installUpdate();
        }}
      >
        Restart to update
      </Button>
    );
  }

  if (status === "downloading") {
    const percent = Math.round(downloadPercent ?? 0);
    if (showProgress) {
      return (
        <div className="flex min-w-40 flex-col gap-1">
          <span className="text-[11px] text-gray-11">
            Downloading… {percent}%
          </span>
          <Progress value={percent} />
        </div>
      );
    }
    return (
      <Button variant="outline" size="sm" disabled>
        Downloading… {percent}%
      </Button>
    );
  }

  if (status === "available") {
    return (
      <Button
        variant="primary"
        size="sm"
        disabled={isDownloadPending}
        onClick={() => {
          trackClick();
          runDownload(undefined);
        }}
      >
        Update now
      </Button>
    );
  }

  if (status === "error") {
    return (
      <Button
        variant="outline"
        size="sm"
        disabled={isCheckPending}
        onClick={() => runCheck(undefined)}
      >
        Retry update check
      </Button>
    );
  }

  return (
    <Button variant="outline" size="sm" disabled>
      Checking for updates…
    </Button>
  );
}
