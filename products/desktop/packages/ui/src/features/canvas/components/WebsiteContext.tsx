import {
  FileTextIcon,
  GitBranchIcon,
  SparkleIcon,
} from "@phosphor-icons/react";
import { FolderInstructionsConflictError } from "@posthog/api-client/posthog-client";
import { buildContextSaveProps } from "@posthog/core/canvas/canvasAnalytics";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  Button as QuillButton,
} from "@posthog/quill";
import { ANALYTICS_EVENTS } from "@posthog/shared/analytics-events";
import type { TaskChannel } from "@posthog/shared/domain-types";
import { useOptionalAuthenticatedClient } from "@posthog/ui/features/auth/authClient";
import { useCurrentUser } from "@posthog/ui/features/auth/useCurrentUser";
import { ChannelHeader } from "@posthog/ui/features/canvas/components/ChannelHeader";
import { CreateChannelModal } from "@posthog/ui/features/canvas/components/CreateChannelModal";
import { channelPageIcon } from "@posthog/ui/features/canvas/components/channelPages";
import { RepositoriesField } from "@posthog/ui/features/canvas/components/RepositoriesField";
import { useChannels } from "@posthog/ui/features/canvas/hooks/useChannels";
import { useChannelsLayout } from "@posthog/ui/features/canvas/hooks/useChannelsLayout";
import {
  useFolderInstructions,
  useFolderInstructionsMutations,
  useFolderInstructionsVersions,
} from "@posthog/ui/features/canvas/hooks/useFolderInstructions";
import {
  useTaskChannels,
  useUpdateTaskChannelRepositories,
} from "@posthog/ui/features/canvas/hooks/useTaskChannels";
import { MarkdownRenderer } from "@posthog/ui/features/editor/components/MarkdownRenderer";
import { useSetHeaderContent } from "@posthog/ui/hooks/useSetHeaderContent";
import {
  PageHeader,
  PageHeaderChip,
  PageHeaderDescription,
  PageHeaderHeading,
  PageHeaderTitle,
  PageHeaderTitleRow,
} from "@posthog/ui/primitives/PageHeader";
import { track } from "@posthog/ui/shell/analytics";
import {
  Box,
  Button,
  Callout,
  Flex,
  ScrollArea,
  SegmentedControl,
  Select,
  Spinner,
  Text,
  TextArea,
} from "@radix-ui/themes";
import { type ReactNode, useEffect, useMemo, useState } from "react";

type Mode = "rendered" | "edit";

// Initial markdown shown when a channel has no instructions yet — gives both
// humans and agents a structural starting point instead of a blank screen.
const CHANNEL_EMPTY_TEMPLATE =
  "# Channel context\n\nDescribe what lives here.\n";
const SPACE_EMPTY_TEMPLATE = "# Space context\n\nDescribe what lives here.\n";

interface WebsiteContextProps {
  channelId: string;
}

type FolderInstructionVersion = NonNullable<
  ReturnType<typeof useFolderInstructionsVersions>["data"]
>[number];

interface WebsiteContextLoadedView {
  channelId: string;
  channelName: string;
  draft: string;
  emptyTemplate: string;
  hasDraft: boolean;
  hasInstructions: boolean;
  isConflict: boolean;
  isFetchingLatest: boolean;
  isLoadingLatest: boolean;
  isLoadingVersions: boolean;
  isPublishing: boolean;
  latestContent: string;
  latestVersion: number | undefined;
  mode: Mode;
  publishError: Error | null;
  selectedVersion: FolderInstructionVersion | null;
  selectedVersionNumber: number | null;
  setDraft: (value: string) => void;
  setHasDraft: (value: boolean) => void;
  setMode: (value: Mode) => void;
  setSelectedVersionNumber: (value: number | null) => void;
  spacesLayout: boolean;
  taskChannel: TaskChannel | undefined;
  versions: FolderInstructionVersion[] | undefined;
  onSave: () => void;
}

type WebsiteContextToolbarView = Pick<
  WebsiteContextLoadedView,
  | "draft"
  | "hasDraft"
  | "hasInstructions"
  | "isFetchingLatest"
  | "isLoadingLatest"
  | "isLoadingVersions"
  | "isPublishing"
  | "latestContent"
  | "latestVersion"
  | "mode"
  | "selectedVersionNumber"
  | "setDraft"
  | "setHasDraft"
  | "setMode"
  | "setSelectedVersionNumber"
  | "onSave"
> & { versions: FolderInstructionVersion[] };

export function WebsiteContext({ channelId }: WebsiteContextProps) {
  const spacesLayout = useChannelsLayout();
  const emptyTemplate = spacesLayout
    ? SPACE_EMPTY_TEMPLATE
    : CHANNEL_EMPTY_TEMPLATE;
  // Channel name for the empty-state copy (the header reads its own).
  const { channels } = useChannels();
  const channelName =
    channels.find((c) => c.id === channelId)?.name ??
    (spacesLayout ? "Space" : "Channel");
  const { channels: taskChannels } = useTaskChannels();
  const taskChannel = taskChannels.find((channel) => channel.id === channelId);

  const {
    data: latest,
    isLoading: isLoadingLatest,
    isFetching: isFetchingLatest,
    error: latestError,
    // Poll while empty so an agent's CONTEXT.md publish (mid plan-session, via
    // the MCP) replaces the empty state without a manual reload.
  } = useFolderInstructions(channelId, { pollWhileEmpty: true });

  const { data: versions = [], isLoading: isLoadingVersions } =
    useFolderInstructionsVersions(channelId);

  const { publish, isPublishing, publishError } =
    useFolderInstructionsMutations(channelId);

  const [mode, setMode] = useState<Mode>("rendered");
  const [draft, setDraft] = useState("");
  const [hasDraft, setHasDraft] = useState(false);

  const hasInstructions = (latest?.content ?? "").trim().length > 0;

  // Seed the editor draft from the latest content the first time we land on
  // edit mode (or whenever latest changes while we're not actively editing).
  // We don't blow away an in-flight edit just because the cache refetched.
  useEffect(() => {
    if (hasDraft) return;
    setDraft(latest?.content ?? "");
  }, [latest?.content, hasDraft]);

  const headerContent = useMemo(
    () => <ChannelHeader channelId={channelId} page="context" />,
    [channelId],
  );
  useSetHeaderContent(headerContent);

  const onSave = async () => {
    try {
      await publish({
        content: draft,
        // base_version=0 signals "no prior version" to the optimistic
        // concurrency check; otherwise we send the version we started from.
        baseVersion: latest?.version ?? 0,
      });
      track(
        ANALYTICS_EVENTS.CONTEXT_ACTION,
        buildContextSaveProps({ channelId, hasInstructions, success: true }),
      );
      setHasDraft(false);
      setMode("rendered");
    } catch {
      track(
        ANALYTICS_EVENTS.CONTEXT_ACTION,
        buildContextSaveProps({ channelId, hasInstructions, success: false }),
      );
      // Errors surface through `publishError` below; nothing to do here.
    }
  };

  const isConflict = publishError instanceof FolderInstructionsConflictError;

  // Allow inspecting an older version read-only. When `null`, we're showing
  // either the latest (rendered/edit) or the empty state. Versions are keyed
  // by their number — the version's identity on the channel.
  const [selectedVersionNumber, setSelectedVersionNumber] = useState<
    number | null
  >(null);

  // Picking a past version forces rendered mode and shows that version's
  // metadata; we don't currently fetch the historical content body, so the
  // viewer falls back to "Open latest in editor" when there is no body.
  // (Backend exposes content only via the `latest` endpoint today.)
  const selectedVersion =
    selectedVersionNumber == null
      ? null
      : (versions.find((v) => v.version === selectedVersionNumber) ?? null);

  let content: ReactNode;

  if (isLoadingLatest) {
    content = (
      <Flex align="center" justify="center" className="h-full">
        <Spinner size="2" />
      </Flex>
    );
  } else if (latestError) {
    content = (
      <Flex direction="column" gap="3" p="4">
        <Callout.Root color="red" size="1">
          <Callout.Text>
            Failed to load channel instructions: {latestError.message}
          </Callout.Text>
        </Callout.Root>
      </Flex>
    );
  } else {
    content = (
      <WebsiteContextLoaded
        view={{
          channelId,
          channelName,
          draft,
          emptyTemplate,
          hasDraft,
          hasInstructions,
          isConflict,
          isFetchingLatest,
          isLoadingLatest,
          isLoadingVersions,
          isPublishing,
          latestContent: latest?.content ?? "",
          latestVersion: latest?.version,
          mode,
          publishError,
          selectedVersion,
          selectedVersionNumber,
          setDraft,
          setHasDraft,
          setMode,
          setSelectedVersionNumber,
          spacesLayout,
          taskChannel,
          versions,
          onSave,
        }}
      />
    );
  }

  return content;
}

function WebsiteContextLoaded({ view }: { view: WebsiteContextLoadedView }) {
  const {
    channelId,
    channelName,
    draft,
    emptyTemplate,
    hasInstructions,
    isConflict,
    latestContent,
    latestVersion,
    mode,
    publishError,
    selectedVersion,
    setDraft,
    setHasDraft,
    setMode,
    spacesLayout,
    taskChannel,
  } = view;

  return (
    <Flex direction="column" height="100%" className="overflow-hidden">
      <WebsiteContextPageHeader
        latestVersion={latestVersion}
        spacesLayout={spacesLayout}
      />
      {spacesLayout && taskChannel ? (
        <SpaceRepositories channel={taskChannel} />
      ) : null}
      <WebsiteContextToolbar
        view={{
          draft: view.draft,
          hasDraft: view.hasDraft,
          hasInstructions: view.hasInstructions,
          isFetchingLatest: view.isFetchingLatest,
          isLoadingLatest: view.isLoadingLatest,
          isLoadingVersions: view.isLoadingVersions,
          isPublishing: view.isPublishing,
          latestContent: view.latestContent,
          latestVersion: view.latestVersion,
          mode: view.mode,
          selectedVersionNumber: view.selectedVersionNumber,
          setDraft: view.setDraft,
          setHasDraft: view.setHasDraft,
          setMode: view.setMode,
          setSelectedVersionNumber: view.setSelectedVersionNumber,
          versions: view.versions ?? [],
          onSave: view.onSave,
        }}
      />

      {publishError ? (
        <Box px="4" pt="3">
          <Callout.Root color={isConflict ? "amber" : "red"} size="1">
            <Callout.Text>
              {isConflict
                ? "Someone else saved a newer version. Reload to merge your changes."
                : `Save failed: ${publishError.message}`}
            </Callout.Text>
          </Callout.Root>
        </Box>
      ) : null}

      <WebsiteContextBody
        channelId={channelId}
        channelName={channelName}
        draft={draft}
        emptyTemplate={emptyTemplate}
        hasInstructions={hasInstructions}
        latestContent={latestContent}
        mode={mode}
        selectedVersion={selectedVersion}
        setDraft={setDraft}
        setHasDraft={setHasDraft}
        setMode={setMode}
        spacesLayout={spacesLayout}
      />
    </Flex>
  );
}

function WebsiteContextPageHeader({
  latestVersion,
  spacesLayout,
}: {
  latestVersion: number | undefined;
  spacesLayout: boolean;
}) {
  if (!spacesLayout) {
    return null;
  }

  return (
    <PageHeader>
      <PageHeaderHeading>
        <PageHeaderTitleRow>
          <PageHeaderTitle>Context</PageHeaderTitle>
          {latestVersion != null && (
            <PageHeaderChip icon={channelPageIcon("context", { size: 12 })}>
              v{latestVersion}
            </PageHeaderChip>
          )}
        </PageHeaderTitleRow>
        <PageHeaderDescription>
          Background every agent working in this space reads before it starts —
          what lives here, who cares about it, and how to work on it.
        </PageHeaderDescription>
      </PageHeaderHeading>
    </PageHeader>
  );
}

function WebsiteContextToolbar({ view }: { view: WebsiteContextToolbarView }) {
  const {
    draft,
    hasDraft,
    hasInstructions,
    isFetchingLatest,
    isLoadingLatest,
    isLoadingVersions,
    isPublishing,
    latestContent,
    latestVersion,
    mode,
    selectedVersionNumber,
    setDraft,
    setHasDraft,
    setMode,
    setSelectedVersionNumber,
    versions,
    onSave,
  } = view;

  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      px="4"
      py="2"
      className="shrink-0 border-b border-b-(--gray-5)"
    >
      <Flex align="center" gap="3">
        <SegmentedControl.Root
          value={mode}
          onValueChange={(value) => setMode(value as Mode)}
          size="1"
        >
          <SegmentedControl.Item value="rendered">
            Rendered
          </SegmentedControl.Item>
          <SegmentedControl.Item value="edit">Edit</SegmentedControl.Item>
        </SegmentedControl.Root>

        {isFetchingLatest && !isLoadingLatest ? (
          <Flex align="center" gap="1">
            <Spinner size="1" />
            <Text className="text-[12px] text-gray-10">Refreshing…</Text>
          </Flex>
        ) : null}

        <VersionSelector
          isLoadingVersions={isLoadingVersions}
          latestVersion={latestVersion}
          selectedVersionNumber={selectedVersionNumber}
          setMode={setMode}
          setSelectedVersionNumber={setSelectedVersionNumber}
          versions={versions}
        />
      </Flex>

      {mode === "edit" ? (
        <Flex align="center" gap="2">
          {hasDraft ? (
            <Button
              size="1"
              variant="soft"
              color="gray"
              onClick={() => {
                setDraft(latestContent);
                setHasDraft(false);
              }}
              disabled={isPublishing}
            >
              Discard
            </Button>
          ) : null}
          <Button
            size="1"
            variant="solid"
            onClick={onSave}
            disabled={
              isPublishing ||
              (hasInstructions ? !hasDraft : draft.trim().length === 0)
            }
          >
            {isPublishing ? <Spinner size="1" /> : null}
            Save new version
          </Button>
        </Flex>
      ) : null}
    </Flex>
  );
}

function VersionSelector({
  isLoadingVersions,
  latestVersion,
  selectedVersionNumber,
  setMode,
  setSelectedVersionNumber,
  versions,
}: {
  isLoadingVersions: boolean;
  latestVersion: number | undefined;
  selectedVersionNumber: number | null;
  setMode: (value: Mode) => void;
  setSelectedVersionNumber: (value: number | null) => void;
  versions: FolderInstructionVersion[];
}) {
  if (versions.length === 0) {
    return null;
  }

  const versionItems = versions.reduce<ReactNode[]>((items, version) => {
    if (version.version !== latestVersion) {
      items.push(
        <Select.Item key={version.version} value={String(version.version)}>
          v{version.version} · {formatTimestamp(version.created_at)}
        </Select.Item>,
      );
    }
    return items;
  }, []);

  return (
    <Select.Root
      size="1"
      value={
        selectedVersionNumber != null ? String(selectedVersionNumber) : "latest"
      }
      onValueChange={(value) => {
        if (value === "latest") {
          setSelectedVersionNumber(null);
        } else {
          setSelectedVersionNumber(Number(value));
          setMode("rendered");
        }
      }}
      disabled={isLoadingVersions}
    >
      <Select.Trigger />
      <Select.Content>
        <Select.Item value="latest">
          Latest (v{latestVersion ?? "—"})
        </Select.Item>
        {versionItems}
      </Select.Content>
    </Select.Root>
  );
}

function WebsiteContextBody({
  channelId,
  channelName,
  draft,
  emptyTemplate,
  hasInstructions,
  latestContent,
  mode,
  selectedVersion,
  setDraft,
  setHasDraft,
  setMode,
  spacesLayout,
}: {
  channelId: string;
  channelName: string;
  draft: string;
  emptyTemplate: string;
  hasInstructions: boolean;
  latestContent: string;
  mode: Mode;
  selectedVersion: FolderInstructionVersion | null;
  setDraft: (value: string) => void;
  setHasDraft: (value: boolean) => void;
  setMode: (value: Mode) => void;
  spacesLayout: boolean;
}) {
  if (!selectedVersion && mode === "edit") {
    return (
      <Box p="4" className="flex min-h-0 flex-1">
        <TextArea
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setHasDraft(true);
          }}
          size="2"
          placeholder={
            spacesLayout
              ? "# Space context\n\nWrite markdown describing this space…"
              : "# Channel context\n\nWrite markdown describing this channel…"
          }
          className="min-h-0 flex-1 font-[var(--code-font-family)]"
        />
      </Box>
    );
  }

  return (
    <ScrollArea
      type="auto"
      scrollbars="vertical"
      className="scroll-area-constrain-width min-h-0 flex-1"
    >
      <Box p="4">
        {selectedVersion ? (
          <Callout.Root color="gray" size="1">
            <Callout.Text>
              Viewing v{selectedVersion.version} metadata. Past content is not
              fetched today — switch to "Latest" to read or edit current
              content.
            </Callout.Text>
          </Callout.Root>
        ) : hasInstructions ? (
          <Box className="text-[13px]">
            <MarkdownRenderer content={latestContent} />
          </Box>
        ) : (
          <EmptyState
            channelId={channelId}
            channelName={channelName}
            onCreate={() => {
              setDraft(emptyTemplate);
              setHasDraft(true);
              setMode("edit");
            }}
          />
        )}
      </Box>
    </ScrollArea>
  );
}

function SpaceRepositories({ channel }: { channel: TaskChannel }) {
  const update = useUpdateTaskChannelRepositories();
  const client = useOptionalAuthenticatedClient();
  const { data: currentUser } = useCurrentUser({ client });
  const canEdit = currentUser?.id === channel.created_by?.id;

  return (
    <div className="flex shrink-0 flex-col gap-2 border-b border-b-(--gray-5) px-4 py-3">
      <div className="flex items-center gap-2">
        <GitBranchIcon size={15} className="text-muted-foreground" />
        <span className="font-medium text-[13px]">Repositories</span>
        {update.isPending ? (
          <Spinner size="1" />
        ) : update.error ? (
          <span className="text-[12px] text-red-11">
            Couldn't save. Try again.
          </span>
        ) : null}
      </div>
      <RepositoriesField
        selected={channel.repositories ?? []}
        integrationId={channel.github_integration ?? null}
        disabled={!canEdit || update.isPending}
        onChange={(repositories, githubIntegration) =>
          update.mutate({
            channelId: channel.id,
            githubIntegration,
            repositories,
          })
        }
      />
    </div>
  );
}

function EmptyState({
  channelId,
  channelName,
  onCreate,
}: {
  channelId: string;
  channelName: string;
  onCreate: () => void;
}) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <FileTextIcon size={28} />
        </EmptyMedia>
        <EmptyTitle>No CONTEXT.md yet</EmptyTitle>
        <EmptyDescription>
          CONTEXT.md tells agents the specific details they need to know when
          working in <strong>{channelName}</strong> — conventions, gotchas, key
          files, and anything else that isn't obvious from the code.
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <Flex align="center" gap="3">
          <QuillButton variant="primary" size="default" onClick={onCreate}>
            Write it myself
          </QuillButton>
          <GenerateWithAgent channelId={channelId} channelName={channelName} />
        </Flex>
      </EmptyContent>
    </Empty>
  );
}

// Opens the describe-and-plan dialog for this (already-existing) context, which
// launches a plan-mode session that investigates PostHog + the repo and publishes
// CONTEXT.md via the MCP once the user approves the plan. Same flow as creating a
// context from scratch, minus the name field.
function GenerateWithAgent({
  channelId,
  channelName,
}: {
  channelId: string;
  channelName: string;
}) {
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <>
      <QuillButton
        variant="outline"
        size="default"
        onClick={() => setDialogOpen(true)}
      >
        <SparkleIcon size={14} />
        Build with agent
      </QuillButton>
      <CreateChannelModal
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        existingContext={{ channelId, channelName }}
      />
    </>
  );
}

// `created_at` is an ISO timestamp; we render it as a short local string for
// the version dropdown. Falls back to the raw string if Date parsing fails.
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
