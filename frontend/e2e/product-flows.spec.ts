import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("socialease.demoUserId", "demo_user");
  });
});

test("legacy module pages converge on the unified conversation", async ({ page }) => {
  for (const path of ["/practice", "/worksheet", "/support"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/chat$/);
  }
});

test("unified chat keeps confirmed nested modules in one durable timeline", async ({
  page
}) => {
  const now = "2026-07-27T08:00:00Z";
  const conversation = {
    conversation_id: "conversation_1",
    user_id: "demo_user",
    title: "新对话",
    status: "active",
    active_module_depth: 0,
    version: 1,
    created_at: now,
    updated_at: now,
    history_notice_version: "2026-07-01"
  };
  const events: Array<Record<string, unknown>> = [];
  let stack: Array<Record<string, unknown>> = [];
  let created = false;
  let messageCount = 0;

  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/imports/legacy-roleplay")) {
      await route.fulfill({
        json: {
          user_id: "demo_user",
          scanned_count: 0,
          imported_count: 0,
          conversations: []
        }
      });
      return;
    }
    if (path === "/api/conversations" && request.method() === "GET") {
      await route.fulfill({
        json: { items: created ? [conversation] : [], next_cursor: null }
      });
      return;
    }
    if (path === "/api/conversations" && request.method() === "POST") {
      expect(request.postDataJSON().history_notice_acknowledged).toBe(true);
      created = true;
      await route.fulfill({ json: conversation });
      return;
    }
    if (path.endsWith("/messages")) {
      messageCount += 1;
      const moduleType = messageCount === 1 ? "roleplay" : "exposure";
      const proposalId = `proposal_${messageCount}`;
      const start = events.length + 1;
      const appended = [
        event(`event_${start}`, start, "user_message", "user", request.postDataJSON().message),
        event(
          `event_${start + 1}`,
          start + 1,
          "module_proposed",
          "assistant",
          "系统只提供模块选项，由用户决定是否进入。",
          { proposal_id: proposalId }
        )
      ];
      events.push(...appended);
      await route.fulfill({
        json: {
          conversation,
          appended_events: appended,
          active_module_stack: stack,
          pending_module_proposal: proposal(proposalId, moduleType, now),
          response: appended[1].content,
          safety_result: safety(),
          context_diagnostics: diagnostics()
        }
      });
      return;
    }
    if (path.endsWith("/accept")) {
      const moduleType = path.includes("proposal_1") ? "roleplay" : "exposure";
      const depth = stack.length + 1;
      stack = [
        ...stack.map((item) => ({ ...item, status: "suspended" })),
        run(`module_${depth}`, moduleType, depth, depth === 1 ? null : "module_1", now)
      ];
      const lifecycle = event(
        `event_${events.length + 1}`,
        events.length + 1,
        "module_started",
        "system",
        `已进入${moduleType === "roleplay" ? "角色扮演" : "分级练习"}。`
      );
      events.push(lifecycle);
      await route.fulfill({
        json: {
          conversation: { ...conversation, active_module_depth: depth },
          active_module_stack: stack,
          appended_events: [lifecycle],
          response: lifecycle.content
        }
      });
      return;
    }
    if (path.endsWith("/terminate")) {
      stack = stack.slice(0, -1).map((item) => ({ ...item, status: "active" }));
      const lifecycle = event(
        `event_${events.length + 1}`,
        events.length + 1,
        "module_terminated",
        "system",
        "已结束当前模块并返回上一层。"
      );
      events.push(lifecycle);
      await route.fulfill({
        json: {
          conversation: { ...conversation, active_module_depth: stack.length },
          active_module_stack: stack,
          appended_events: [lifecycle],
          response: lifecycle.content
        }
      });
      return;
    }
    if (path === "/api/conversations/conversation_1") {
      await route.fulfill({
        json: {
          conversation: { ...conversation, active_module_depth: stack.length },
          events: { items: events, next_cursor: null },
          active_module_stack: stack,
          pending_module_proposals: []
        }
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.getByRole("button", { name: "新建对话" }).click();
  await page.getByLabel("我已了解上述保存、导出和删除方式。").check();
  await page.getByRole("button", { name: "了解并新建" }).click();
  await page.locator("textarea").fill("我想练习和同学打招呼");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("是否进入角色扮演？")).toBeVisible();
  await page.getByRole("button", { name: "确认进入" }).click();

  await page.locator("textarea").fill("能否进入低强度分级练习");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("是否进入分级练习？")).toBeVisible();
  await page.getByRole("button", { name: "确认进入" }).click();
  await expect(page.getByText("角色扮演", { exact: true })).toBeVisible();
  await expect(page.getByText("分级练习", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText("我想练习和同学打招呼")).toBeVisible();
  await page.getByRole("button", { name: "结束当前模块" }).click();
  await expect(page.getByText("已结束当前模块并返回上一层。")).toBeVisible();
});

function event(
  eventId: string,
  sequence: number,
  eventType: string,
  role: string,
  content: unknown,
  structuredPayload: Record<string, unknown> | null = null
) {
  return {
    event_id: eventId,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    sequence_no: sequence,
    event_type: eventType,
    role,
    content,
    structured_payload: structuredPayload,
    module_run_id: null,
    parent_module_run_id: null,
    idempotency_key: eventId,
    created_at: "2026-07-27T08:00:00Z"
  };
}

function proposal(id: string, moduleType: string, now: string) {
  return {
    proposal_id: id,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    proposed_module: moduleType,
    reason_code: "explicit_practice_request",
    bounded_parameters: {
      kind: moduleType,
      ...(moduleType === "roleplay"
        ? { scenario_description: "练习打招呼", difficulty: 2 }
        : { goal: "低强度练习", starting_anxiety: 4 })
    },
    status: "pending",
    request_hash: "a".repeat(64),
    expires_at: now,
    created_at: now
  };
}

function run(
  id: string,
  moduleType: string,
  depth: number,
  parentId: string | null,
  now: string
) {
  return {
    module_run_id: id,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    module_type: moduleType,
    parent_module_run_id: parentId,
    depth,
    status: "active",
    module_parameters: { kind: moduleType },
    domain_session_id: `domain_${id}`,
    started_at: now,
    ended_at: null,
    version: 1
  };
}

function safety() {
  return { risk_level: "low", reason: "test", matched_terms: [] };
}

function diagnostics() {
  return {
    conversation_id_hash: "0000000000000000",
    recent_event_count: 0,
    recent_event_sequence_start: null,
    recent_event_sequence_end: null,
    compact_summary_version: null,
    active_module_count: 0,
    selected_memory_count: 0,
    estimated_tokens: 0,
    total_token_budget: 6000,
    budget_profile: "ordinary",
    dropped_sections: [],
    tokenizer_backend: "test",
    context_backend: "database",
    cache_status: "database",
    active_overlay_type: null,
    active_overlay_version: null,
    parent_resume_projection_count: 0
  };
}
