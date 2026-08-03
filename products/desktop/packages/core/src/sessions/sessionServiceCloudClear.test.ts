import type { AgentSession } from "@posthog/shared";
import { describe, expect, it, vi } from "vitest";
import { POSTHOG_NOTIFICATIONS } from "./acpNotifications";
import { SessionService, type SessionServiceDeps } from "./sessionService";

const TASK_ID = "task-1";
const TASK_RUN_ID = `run-${TASK_ID}`;

function createHarness({
  conversationClear = true,
}: {
  conversationClear?: boolean;
} = {}) {
  const sessions: Record<string, AgentSession> = {
    [TASK_RUN_ID]: {
      taskRunId: TASK_RUN_ID,
      taskId: TASK_ID,
      taskTitle: "Test task",
      channel: "",
      events: [],
      startedAt: 1,
      status: "connected",
      isCloud: true,
      cloudStatus: "completed",
      conversationClear,
      isPromptPending: false,
      isCompacting: false,
      promptStartedAt: null,
      pendingPermissions: new Map(),
      pausedDurationMs: 0,
      messageQueue: [],
      optimisticItems: [],
    } as unknown as AgentSession,
  };

  const appendEvents = vi.fn();
  const clearTaskRunConversation = vi.fn().mockResolvedValue(undefined);
  const runTaskInCloud = vi.fn();

  const deps = {
    store: {
      getSessions: () => sessions,
      getSessionByTaskId: (taskId: string) =>
        Object.values(sessions).find((s) => s.taskId === taskId),
      appendEvents,
      updateSession: vi.fn(),
      appendOptimisticItem: vi.fn(),
      clearTailOptimisticItems: vi.fn(),
    },
    h: {
      getCloudPromptTransport: (prompt: string) => ({
        promptText: prompt,
        messageText: prompt,
        filePaths: [],
        skillBundles: [],
      }),
    },
    log: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() },
    toast: { error: vi.fn(), info: vi.fn() },
    track: vi.fn(),
    getIsOnline: () => true,
    addDirectoryDialog: { open: false },
    getAuthenticatedClient: async () => ({
      clearTaskRunConversation,
      runTaskInCloud,
    }),
    trpc: {
      agent: {
        onSessionIdleKilled: { subscribe: () => ({ unsubscribe: vi.fn() }) },
      },
    },
  } as unknown as SessionServiceDeps;

  return {
    service: new SessionService(deps),
    appendEvents,
    clearTaskRunConversation,
    runTaskInCloud,
  };
}

describe("SessionService /clear on a finished cloud run", () => {
  it("records the boundary and renders it without resuming into a new run", async () => {
    const { service, appendEvents, clearTaskRunConversation, runTaskInCloud } =
      createHarness();

    const result = await service.sendPrompt(TASK_ID, "/clear");

    expect(result).toEqual({ stopReason: "end_turn" });
    expect(clearTaskRunConversation).toHaveBeenCalledWith(TASK_ID, TASK_RUN_ID);
    // Resuming would spin a whole sandbox to clear a conversation the next run
    // rebuilds from the log anyway.
    expect(runTaskInCloud).not.toHaveBeenCalled();

    // A finished run streams nothing back, so the thread is painted from here.
    const [, events] = appendEvents.mock.calls[0];
    expect(
      events.map((e: { message: { method: string } }) => e.message.method),
    ).toEqual(["session/update", POSTHOG_NOTIFICATIONS.CONVERSATION_CLEARED]);
  });

  it("resumes into a new run when the agent cannot honour the boundary", async () => {
    // An older agent ignores the marker and resumes the conversation it was meant to
    // retire, so recording one would claim a clear that never happens.
    const { service, clearTaskRunConversation } = createHarness({
      conversationClear: false,
    });

    await service.sendPrompt(TASK_ID, "/clear").catch(() => undefined);

    expect(clearTaskRunConversation).not.toHaveBeenCalled();
  });

  it("still resumes into a new run for an ordinary message", async () => {
    const { service, clearTaskRunConversation } = createHarness();

    await service.sendPrompt(TASK_ID, "keep going").catch(() => undefined);

    expect(clearTaskRunConversation).not.toHaveBeenCalled();
  });
});
