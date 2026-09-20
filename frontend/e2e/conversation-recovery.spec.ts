import { expect, test } from "@playwright/test";

const now = "2026-09-13T08:00:00Z";
const conversation = {
  conversation_id: "recovery_conversation",
  user_id: "demo_user",
  title: "恢复测试",
  status: "active",
  active_module_depth: 0,
  version: 1,
  created_at: now,
  updated_at: now,
  history_notice_version: "2026-07-01"
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem("recovery-test-initialized")) {
      window.localStorage.clear();
      window.sessionStorage.setItem("recovery-test-initialized", "true");
    }
    window.localStorage.setItem("socialease.demoUserId", "demo_user");
  });
});

test("shows an unpersisted crisis reply and reuses its key until persistence succeeds", async ({
  page
}) => {
  const keys: string[] = [];
  let attempt = 0;
  const crisisEvent = event("crisis_event", 2, "crisis_escalated", "system", "静态危机安全指引：请联系可信任的人或当地紧急服务。");
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations" && request.method() === "GET") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await route.fulfill({
        json: {
          conversation,
          events: { items: [], next_cursor: null },
          active_module_stack: [],
          pending_module_proposals: [],
          intent_plan: null
        }
      });
      return;
    }
    if (path.endsWith("/messages/stream")) {
      attempt += 1;
      keys.push(request.postDataJSON().idempotency_key);
      const result = {
        conversation,
        appended_events: attempt === 1 ? [] : [crisisEvent],
        active_module_stack: [],
        pending_module_proposal: null,
        transient_response:
          attempt === 1
            ? "静态危机安全指引：请联系可信任的人或当地紧急服务。"
            : null,
        crisis_persistence_status:
          attempt === 1 ? "degraded_retry_required" : "persisted"
      };
      await route.fulfill({
        contentType: "text/event-stream",
        body: `event: response_completed\ndata: ${JSON.stringify({ type: "response_completed", result })}\n\n`
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("我现在需要紧急帮助");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/静态危机安全指引/)).toBeVisible();
  await expect(page.getByText(/尚未保存/)).toBeVisible();
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("安全升级")).toBeVisible();
  await expect(page.getByText(/尚未保存/)).toHaveCount(0);
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  expect(
    await page.evaluate(() =>
      window.localStorage.getItem("socialease:conversation-command-recovery")
    )
  ).toBeNull();
});

test("keeps the recovery key when crisis guidance is persisted but safety state is partial", async ({
  page
}) => {
  const keys: string[] = [];
  let attempt = 0;
  const crisisEvent = event(
    "partial_crisis_event",
    2,
    "crisis_escalated",
    "system",
    "静态危机安全指引：请联系可信任的人或当地紧急服务。"
  );
  await routeConversationShell(page, async (route) => {
    attempt += 1;
    keys.push(route.request().postDataJSON().idempotency_key);
    await route.fulfill({
      contentType: "text/event-stream",
      body: completedFrame({
        conversation,
        appended_events: [crisisEvent],
        active_module_stack: [],
        pending_module_proposal: null,
        transient_response: null,
        crisis_persistence_status:
          attempt === 1 ? "degraded_retry_required" : "persisted"
      })
    });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("我现在需要紧急帮助");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("安全升级")).toBeVisible();
  await expect(page.getByText(/状态尚未完整保存/)).toBeVisible();
  expect(
    await page.evaluate(() =>
      window.localStorage.getItem("socialease:conversation-command-recovery")
    )
  ).not.toBeNull();

  await page.getByRole("button", { name: "发送" }).click();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  await expect(page.getByText(/状态尚未完整保存/)).toHaveCount(0);
});

test("recovers a completed command after refresh without storing the user message", async ({
  page
}) => {
  const recoveredEvent = event(
    "recovered_event",
    1,
    "assistant_message",
    "assistant",
    "已从持久化命令结果恢复。"
  );
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "socialease:conversation-command-recovery",
      JSON.stringify({
        conversationId: "recovery_conversation",
        idempotencyKey: "refresh-command-001"
      })
    );
  });
  await page.route("**/api/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path.endsWith("/commands/refresh-command-001")) {
      await route.fulfill({
        json: {
          status: "completed",
          result: {
            conversation,
            appended_events: [recoveredEvent],
            active_module_stack: [],
            pending_module_proposal: null,
            transient_response: null,
            crisis_persistence_status: "not_applicable"
          },
          can_retry_same_request: false,
          can_abandon: false
        }
      });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await route.fulfill({
        json: {
          conversation,
          events: { items: [], next_cursor: null },
          active_module_stack: [],
          pending_module_proposals: [],
          intent_plan: null
        }
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await expect(page.getByText("已从持久化命令结果恢复。")).toBeVisible();
  const receipt = await page.evaluate(() =>
    window.localStorage.getItem("socialease:conversation-command-recovery")
  );
  expect(receipt).toBeNull();
});

test("uses a new key when the restored input is edited", async ({ page }) => {
  const keys: string[] = [];
  let abandonAttempts = 0;
  let firstKey = "";
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await fulfillConversationDetail(route);
      return;
    }
    if (path.endsWith(`/commands/${firstKey}/abandon`)) {
      abandonAttempts += 1;
      if (abandonAttempts === 1) {
        await route.fulfill({ status: 409, json: { detail: "still processing" } });
      } else {
        await route.fulfill({
          json: commandRecovery("abandoned", "not_required", false)
        });
      }
      return;
    }
    if (firstKey && path.endsWith(`/commands/${firstKey}`)) {
      await route.fulfill({
        json: commandRecovery("recoverable", "not_required", true)
      });
      return;
    }
    if (path.endsWith("/messages/stream")) {
      const key = request.postDataJSON().idempotency_key;
      keys.push(key);
      firstKey ||= key;
      if (keys.length === 1) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: 'event: response_error\ndata: {"type":"response_error","detail":"temporary failure"}\n\n'
        });
        return;
      }
      await route.fulfill({
        contentType: "text/event-stream",
        body: completedFrame({
          conversation,
          appended_events: [],
          active_module_stack: [],
          pending_module_proposal: null,
          transient_response: null,
          crisis_persistence_status: "not_applicable"
        })
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("原消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("temporary failure")).toBeVisible();
  await page.locator("textarea").fill("已经修改的消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("still processing")).toBeVisible();

  expect(keys).toHaveLength(1);
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(firstKey);

  await page.reload();
  await expect(page.getByText(/上一条请求未完成/)).toBeVisible();
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(firstKey);
  await page.locator("textarea").fill("已经修改的消息");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => keys.length).toBe(2);
  expect(keys[1]).not.toBe(keys[0]);
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBeNull();
});

test("does not abandon pending crisis recovery after refresh", async ({
  page
}) => {
  let abandoned = false;
  let submittedKey: string | null = null;
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "socialease:conversation-command-recovery",
      JSON.stringify({
        conversationId: "recovery_conversation",
        idempotencyKey: "expired-refresh-command"
      })
    );
  });
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await fulfillConversationDetail(route);
      return;
    }
    if (path.endsWith("/commands/expired-refresh-command/abandon")) {
      abandoned = true;
      await route.fulfill({
        json: {
          status: "abandoned",
          result: null,
          can_retry_same_request: false,
          can_abandon: false,
          safety_recovery_status: "recovery_pending"
        }
      });
      return;
    }
    if (path.endsWith("/commands/expired-refresh-command")) {
      await route.fulfill({
        json: {
          status: "recoverable",
          result: null,
          can_retry_same_request: true,
          can_abandon: false,
          safety_recovery_status: "recovery_pending"
        }
      });
      return;
    }
    if (path.endsWith("/messages/stream")) {
      submittedKey = request.postDataJSON().idempotency_key;
      await route.fulfill({
        contentType: "text/event-stream",
        body: 'event: response_error\ndata: {"type":"response_error","detail":"idempotency key was reused with different input"}\n\n'
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await expect(page.getByText(/仍需恢复安全状态/)).toBeVisible();
  expect(abandoned).toBe(false);
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(
    "expired-refresh-command"
  );
  await page.locator("textarea").fill("一条不同的普通消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/reused with different input/)).toBeVisible();
  expect(abandoned).toBe(false);
  expect(submittedKey).toBe("expired-refresh-command");
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(
    "expired-refresh-command"
  );
});

test("reuses an owner-verified missing command key after refresh", async ({
  page
}) => {
  const keys: string[] = [];
  let streamAttempt = 0;
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await fulfillConversationDetail(route);
      return;
    }
    if (path.includes("/commands/")) {
      await route.fulfill({
        json: {
          status: "not_found",
          result: null,
          can_retry_same_request: true,
          can_abandon: false,
          safety_recovery_status: null
        }
      });
      return;
    }
    if (path.endsWith("/messages/stream")) {
      streamAttempt += 1;
      keys.push(request.postDataJSON().idempotency_key);
      if (streamAttempt === 1) {
        await route.fulfill({
          contentType: "text/event-stream",
          body: 'event: response_error\ndata: {"type":"response_error","detail":"request never reached server"}\n\n'
        });
      } else {
        await route.fulfill({
          contentType: "text/event-stream",
          body: completedFrame({
            conversation,
            appended_events: [],
            active_module_stack: [],
            pending_module_proposal: null,
            transient_response: null,
            crisis_persistence_status: "not_applicable"
          })
        });
      }
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("未到达服务端的消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("request never reached server")).toBeVisible();
  await page.reload();
  await expect(page.getByText(/未到达服务端/)).toBeVisible();
  await page.locator("textarea").fill("未到达服务端的消息");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => keys.length).toBe(2);
  expect(keys[1]).toBe(keys[0]);
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBeNull();
});

test("recovers a transient crisis whose command was never created", async ({
  page
}) => {
  const keys: string[] = [];
  let streamAttempt = 0;
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await fulfillConversationDetail(route);
      return;
    }
    if (path.includes("/commands/")) {
      await route.fulfill({
        json: {
          status: "not_found",
          result: null,
          can_retry_same_request: true,
          can_abandon: false,
          safety_recovery_status: null
        }
      });
      return;
    }
    if (path.endsWith("/messages/stream")) {
      streamAttempt += 1;
      keys.push(request.postDataJSON().idempotency_key);
      await route.fulfill({
        contentType: "text/event-stream",
        body: completedFrame({
          conversation,
          appended_events: [],
          active_module_stack: [],
          pending_module_proposal: null,
          transient_response:
            streamAttempt === 1 ? "静态危机安全指引：请联系可信任的人或当地紧急服务。" : null,
          crisis_persistence_status:
            streamAttempt === 1 ? "degraded_retry_required" : "persisted"
        })
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("我现在需要紧急帮助");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/静态危机安全指引/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/未到达服务端/)).toBeVisible();
  await page.locator("textarea").fill("我现在需要紧急帮助");
  await page.getByRole("button", { name: "发送" }).click();

  await expect.poll(() => keys.length).toBe(2);
  expect(keys[1]).toBe(keys[0]);
  expect(await storedReceiptKey(page, conversation.conversation_id)).toBeNull();
});

for (const failure of ["server", "timeout"] as const) {
  test(`keeps the receipt when command lookup has a ${failure} failure`, async ({
    page
  }) => {
    let messageRequests = 0;
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "socialease:conversation-command-recovery",
        JSON.stringify({
          conversationId: "recovery_conversation",
          idempotencyKey: "unknown-recovery-command"
        })
      );
    });
    await page.route("**/api/conversations**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/conversations") {
        await route.fulfill({ json: { items: [conversation], next_cursor: null } });
        return;
      }
      if (path === "/api/conversations/recovery_conversation") {
        await fulfillConversationDetail(route);
        return;
      }
      if (path.endsWith("/commands/unknown-recovery-command")) {
        if (failure === "server") {
          await route.fulfill({ status: 503, json: { detail: "temporary outage" } });
        } else {
          await route.abort("timedout");
        }
        return;
      }
      if (path.endsWith("/messages/stream")) {
        messageRequests += 1;
      }
      await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
    });

    await page.goto("/chat");
    await page.locator("textarea").fill("不能越过未知状态");
    await page.getByRole("button", { name: "发送" }).click();

    expect(messageRequests).toBe(0);
    expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(
      "unknown-recovery-command"
    );
  });
}

test("keeps independent recovery receipts when switching conversations", async ({
  page
}) => {
  const secondConversation = {
    ...conversation,
    conversation_id: "second_recovery_conversation",
    title: "第二段恢复测试"
  };
  const keys = new Map<string, string>();
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({
        json: { items: [conversation, secondConversation], next_cursor: null }
      });
      return;
    }
    for (const item of [conversation, secondConversation]) {
      if (path === `/api/conversations/${item.conversation_id}`) {
        await route.fulfill({
          json: {
            conversation: item,
            events: { items: [], next_cursor: null },
            active_module_stack: [],
            pending_module_proposals: [],
            intent_plan: null
          }
        });
        return;
      }
      if (path === `/api/conversations/${item.conversation_id}/messages/stream`) {
        keys.set(item.conversation_id, request.postDataJSON().idempotency_key);
        await route.fulfill({
          contentType: "text/event-stream",
          body: 'event: response_error\ndata: {"type":"response_error","detail":"temporary failure"}\n\n'
        });
        return;
      }
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("第一段待恢复消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("temporary failure")).toBeVisible();
  await page.getByRole("button", { name: /第二段恢复测试/ }).click();
  await page.locator("textarea").fill("第二段待恢复消息");
  await page.getByRole("button", { name: "发送" }).click();
  await expect.poll(() => keys.size).toBe(2);

  expect(await storedReceiptKey(page, conversation.conversation_id)).toBe(
    keys.get(conversation.conversation_id)
  );
  expect(await storedReceiptKey(page, secondConversation.conversation_id)).toBe(
    keys.get(secondConversation.conversation_id)
  );
  const stored = await page.evaluate(() =>
    window.localStorage.getItem("socialease:conversation-command-recovery")
  );
  expect(stored).not.toContain("第一段待恢复消息");
  expect(stored).not.toContain("第二段待恢复消息");
});

async function routeConversationShell(
  page: import("@playwright/test").Page,
  onMessage: (route: import("@playwright/test").Route) => Promise<void>
) {
  await page.route("**/api/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/conversations") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/recovery_conversation") {
      await fulfillConversationDetail(route);
      return;
    }
    if (path.endsWith("/messages/stream")) {
      await onMessage(route);
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });
}

async function fulfillConversationDetail(
  route: import("@playwright/test").Route
) {
  await route.fulfill({
    json: {
      conversation,
      events: { items: [], next_cursor: null },
      active_module_stack: [],
      pending_module_proposals: [],
      intent_plan: null
    }
  });
}

function completedFrame(result: Record<string, unknown>): string {
  return `event: response_completed\ndata: ${JSON.stringify({ type: "response_completed", result })}\n\n`;
}

function commandRecovery(
  status: "recoverable" | "abandoned",
  safetyRecoveryStatus: "not_required" | "recovery_pending",
  canAbandon: boolean
) {
  return {
    status,
    result: null,
    can_retry_same_request: status === "recoverable",
    can_abandon: canAbandon,
    safety_recovery_status: safetyRecoveryStatus
  };
}

async function storedReceiptKey(
  page: import("@playwright/test").Page,
  conversationId: string
): Promise<string | null> {
  return page.evaluate((id) => {
    const raw = window.localStorage.getItem(
      "socialease:conversation-command-recovery"
    );
    if (!raw) {
      return null;
    }
    const value = JSON.parse(raw);
    return value.receipts?.[id]?.idempotencyKey ?? value.idempotencyKey ?? null;
  }, conversationId);
}

function event(
  eventId: string,
  sequence: number,
  eventType: string,
  role: string,
  content: string
) {
  return {
    event_id: eventId,
    conversation_id: conversation.conversation_id,
    user_id: conversation.user_id,
    sequence_no: sequence,
    event_type: eventType,
    role,
    content,
    structured_payload: null,
    module_run_id: null,
    parent_module_run_id: null,
    idempotency_key: eventId,
    created_at: now
  };
}
