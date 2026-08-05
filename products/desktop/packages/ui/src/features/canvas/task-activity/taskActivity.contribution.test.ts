import type { TaskActivityPage } from "@posthog/shared/domain-types";
import { AUTH_SCOPED_QUERY_META } from "@posthog/ui/features/auth/useCurrentUser";
import type {
  NotificationBus,
  TaskActivitySignal,
} from "@posthog/ui/features/notifications/notifications";
import { type InfiniteData, QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const markTaskActivityRead = vi.hoisted(() => vi.fn());
vi.mock("@posthog/ui/features/auth/authClientImperative", () => ({
  getAuthenticatedClient: () => Promise.resolve({ markTaskActivityRead }),
}));

import { TaskActivityContribution } from "./taskActivity.contribution";

let activityListener: ((signal: TaskActivitySignal) => void) | undefined;
const notificationBus = {
  subscribeToTaskActivity: vi.fn(
    (listener: (signal: TaskActivitySignal) => void) => {
      activityListener = listener;
      return vi.fn();
    },
  ),
} as unknown as NotificationBus;

describe("TaskActivityContribution", () => {
  let queryClient: QueryClient;
  let contribution: TaskActivityContribution;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient();
    contribution = new TaskActivityContribution(notificationBus, queryClient);
    contribution.start();
  });

  it("shows task activity immediately when its backend projection is not available yet", () => {
    queryClient.setQueryDefaults(["task-activity"], {
      meta: AUTH_SCOPED_QUERY_META,
    });
    queryClient.setQueryData<InfiniteData<TaskActivityPage>>(
      ["task-activity"],
      {
        pages: [{ results: [], unread_count: 0 }],
        pageParams: [undefined],
      },
    );

    activityListener?.({
      taskId: "task-1",
      taskTitle: "Channel task",
      activityKind: "awaiting_input",
      activityAt: "2026-07-27T10:00:00Z",
      isUnread: true,
    });

    const cached = queryClient.getQueryData<InfiniteData<TaskActivityPage>>([
      "task-activity",
    ]);
    expect(cached?.pages[0]).toMatchObject({
      unread_count: 1,
      results: [
        {
          task_id: "task-1",
          task_title: "Channel task",
          activity_kind: "awaiting_input",
          is_unread: true,
        },
      ],
    });
  });

  it("replaces an unread row with a read one when the activity was already seen", () => {
    queryClient.setQueryDefaults(["task-activity"], {
      meta: AUTH_SCOPED_QUERY_META,
    });
    queryClient.setQueryData<InfiniteData<TaskActivityPage>>(
      ["task-activity"],
      {
        pages: [
          {
            results: [
              {
                id: "activity-1",
                task_id: "task-1",
                task_title: "Channel task",
                activity_at: "2026-07-27T09:00:00Z",
                activity_kind: "awaiting_input",
                snippet: "",
                is_unread: true,
              },
            ],
            unread_count: 1,
          },
        ],
        pageParams: [undefined],
      },
    );

    activityListener?.({
      taskId: "task-1",
      taskTitle: "Channel task",
      activityKind: "completed",
      activityAt: "2026-07-27T10:00:00Z",
      isUnread: false,
    });

    const cached = queryClient.getQueryData<InfiniteData<TaskActivityPage>>([
      "task-activity",
    ]);
    expect(cached?.pages[0]).toMatchObject({
      unread_count: 0,
      results: [
        {
          task_id: "task-1",
          activity_kind: "completed",
          is_unread: false,
        },
      ],
    });
  });

  it.each([
    ["born-read activity advances the server read cursor", false, 1],
    ["unread activity leaves the server cursor alone", true, 0],
  ])("%s", async (_label, isUnread, persistCalls) => {
    activityListener?.({
      taskId: "task-1",
      taskTitle: "Channel task",
      activityKind: "completed",
      activityAt: "2026-07-27T10:00:00Z",
      isUnread,
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(markTaskActivityRead).toHaveBeenCalledTimes(persistCalls);
    if (persistCalls > 0) {
      expect(markTaskActivityRead).toHaveBeenCalledWith([
        { task_id: "task-1", seen_before: "2026-07-27T10:00:00Z" },
      ]);
    }
  });

  it("does not recreate activity data after the authenticated query is removed", () => {
    activityListener?.({
      taskId: "task-1",
      taskTitle: "Previous user's task",
      activityKind: "completed",
      activityAt: "2026-07-27T10:00:00Z",
      isUnread: true,
    });

    expect(queryClient.getQueryData(["task-activity"])).toBeUndefined();
  });
});
