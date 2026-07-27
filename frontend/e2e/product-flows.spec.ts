import { expect, test, type Page, type Route } from "@playwright/test";

const API = "http://127.0.0.1:8000/api";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.localStorage.setItem("socialease.demoUserId", "demo_user");
  });
});

test("login stores account session and reaches dashboard", async ({ page }) => {
  await page.route(`${API}/auth/login`, async (route) => {
    await route.fulfill({
      json: {
        user: { user_id: "user_account_1", email: "pilot@example.com" },
        tokens: {
          access_token: "access-token",
          refresh_token: "refresh-token",
          token_type: "bearer",
          expires_in: 3600
        }
      }
    });
  });

  await page.goto("/login");
  await page.getByLabel("邮箱").fill("pilot@example.com");
  await page.getByLabel("密码").fill("password123");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText("已登录：pilot@example.com")).toBeVisible();
});

test("invite registration sends invite code and reaches dashboard", async ({ page }) => {
  await page.route(`${API}/auth/register`, async (route) => {
    const payload = route.request().postDataJSON() as {
      email: string;
      password: string;
      invite_code?: string;
    };
    expect(payload.invite_code).toBe("pilot-code");
    await route.fulfill({
      json: {
        user: { user_id: "user_invite_1", email: payload.email },
        tokens: {
          access_token: "invite-access-token",
          refresh_token: "invite-refresh-token",
          token_type: "bearer",
          expires_in: 3600
        }
      }
    });
  });

  await page.goto("/login");
  await page.getByRole("button", { name: /创建账号|邀请码注册/ }).click();
  await page.getByLabel("邮箱").fill("new-pilot@example.com");
  await page.getByLabel("密码").fill("password123");
  await page.getByLabel("邀请码").fill("pilot-code");
  await page.getByRole("button", { name: "注册" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText("已登录：new-pilot@example.com")).toBeVisible();
});

test("dashboard shows product status and next step", async ({ page }) => {
  await mockDashboard(page);

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "练习工作台" })).toBeVisible();
  await expect(page.getByText("已确认边界")).toBeVisible();
  await expect(page.getByText("进行中")).toHaveCount(2);
  await expect(page.getByText("课堂发言开场练习")).toBeVisible();
  await expect(page.getByText("下次先练一句短开场。")).toBeVisible();
  await expect(page.getByRole("link", { name: "继续当前计划" })).toBeVisible();
  await expect(page.getByRole("link", { name: "查看历史" })).toBeVisible();
  await expect(page.getByText("risk", { exact: false })).toHaveCount(0);
  await expect(page.getByText("run_id", { exact: false })).toHaveCount(0);
});

test("privacy page explains non-medical boundary and data controls", async ({ page }) => {
  await page.goto("/privacy");

  await expect(page.getByRole("heading", { name: "隐私和数据说明" })).toBeVisible();
  await expect(page.getByText("非医疗产品").first()).toBeVisible();
  await expect(page.getByText("不做诊断，不替代心理咨询")).toBeVisible();
  await expect(page.getByText("设置页支持导出和删除本人记录")).toBeVisible();
});

test("onboarding completes without saving long-term preferences", async ({ page }) => {
  await page.route("**/api/users/*/onboarding", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: {
          user_id: "demo_user",
          onboarding_profile: emptyOnboardingProfile()
        }
      });
      return;
    }
    const payload = route.request().postDataJSON() as {
      onboarding_profile: { preferred_scenario: string; boundary_acknowledged: boolean };
    };
    expect(payload.onboarding_profile.preferred_scenario).toBe("");
    expect(payload.onboarding_profile.boundary_acknowledged).toBe(true);
    await route.fulfill({
      json: {
        user_id: "demo_user",
        onboarding_profile: payload.onboarding_profile
      }
    });
  });

  await page.goto("/onboarding");

  await page.getByLabel(/保存低敏练习偏好/).uncheck();
  await page.getByLabel(/我了解 SocialEase/).check();
  await page.getByRole("button", { name: "完成设置" }).click();

  await expect(page.getByText("长期练习偏好没有保存")).toBeVisible();
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
  let moduleStack: Array<Record<string, unknown>> = [];
  let created = false;
  let messageCount = 0;

  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/conversations" && request.method() === "GET") {
      await route.fulfill({
        json: { items: created ? [conversation] : [], next_cursor: null }
      });
      return;
    }
    if (path === "/api/conversations" && request.method() === "POST") {
      const payload = request.postDataJSON();
      expect(payload.history_notice_acknowledged).toBe(true);
      created = true;
      await route.fulfill({ json: conversation });
      return;
    }
    if (path.endsWith("/messages")) {
      messageCount += 1;
      const moduleType = messageCount === 1 ? "roleplay" : "exposure";
      const proposalId = `proposal_${messageCount}`;
      const startSequence = events.length + 1;
      const appended = [
        conversationEvent(
          `event_${startSequence}`,
          startSequence,
          "user_message",
          "user",
          request.postDataJSON().message
        ),
        conversationEvent(
          `event_${startSequence + 1}`,
          startSequence + 1,
          "module_proposed",
          "assistant",
          moduleType === "roleplay"
            ? "我可以提供角色扮演选项，由你决定是否进入。"
            : "我可以在当前角色扮演中加入低强度分级练习。",
          { proposal_id: proposalId }
        )
      ];
      events.push(...appended);
      await route.fulfill({
        json: {
          conversation,
          appended_events: appended,
          active_module_stack: moduleStack,
          pending_module_proposal: moduleProposal(proposalId, moduleType, now),
          response: appended[1].content,
          safety_result: safety("low"),
          context_diagnostics: conversationDiagnostics()
        }
      });
      return;
    }
    if (path.endsWith("/accept")) {
      const moduleType = path.includes("proposal_1") ? "roleplay" : "exposure";
      const depth = moduleStack.length + 1;
      const run = moduleRun(
        `module_${depth}`,
        moduleType,
        depth,
        depth === 1 ? null : "module_1",
        now
      );
      moduleStack = [
        ...moduleStack.map((item) => ({ ...item, status: "suspended" })),
        run
      ];
      const event = conversationEvent(
        `event_${events.length + 1}`,
        events.length + 1,
        "module_started",
        "system",
        `已进入${moduleType === "roleplay" ? "角色扮演" : "分级练习"}。`
      );
      events.push(event);
      await route.fulfill({
        json: {
          conversation: { ...conversation, active_module_depth: depth },
          active_module_stack: moduleStack,
          appended_events: [event],
          response: event.content
        }
      });
      return;
    }
    if (path.endsWith("/terminate")) {
      moduleStack = moduleStack.slice(0, -1).map((item) => ({
        ...item,
        status: "active"
      }));
      const event = conversationEvent(
        `event_${events.length + 1}`,
        events.length + 1,
        "module_terminated",
        "system",
        "已结束当前模块并返回上一层。"
      );
      events.push(event);
      await route.fulfill({
        json: {
          conversation: {
            ...conversation,
            active_module_depth: moduleStack.length
          },
          active_module_stack: moduleStack,
          appended_events: [event],
          response: event.content
        }
      });
      return;
    }
    if (path === "/api/conversations/conversation_1") {
      await route.fulfill({
        json: {
          conversation: {
            ...conversation,
            active_module_depth: moduleStack.length
          },
          events: { items: events, next_cursor: null },
          active_module_stack: moduleStack,
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
  await expect(page.getByRole("button", { name: "结束当前模块" })).toBeVisible();

  await page.locator("textarea").fill("能否先做一个更低强度的分级练习");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("是否进入分级练习？")).toBeVisible();
  await page.getByRole("button", { name: "确认进入" }).click();
  await expect(page.getByText("角色扮演", { exact: true })).toBeVisible();
  await expect(page.getByText("分级练习", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText("我想练习和同学打招呼")).toBeVisible();
  await expect(page.getByText("分级练习", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "结束当前模块" }).click();
  await expect(page.getByText("已结束当前模块并返回上一层。")).toBeVisible();
});

test("settings resets onboarding through backend", async ({ page }) => {
  let resetCalled = false;
  await mockProfile(page);
  await page.route(`${API}/users/demo_user/onboarding`, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    resetCalled = true;
    await route.fulfill({
      json: {
        user_id: "demo_user",
        onboarding_profile: emptyOnboardingProfile()
      }
    });
  });

  await page.goto("/settings");
  await page.getByRole("button", { name: "重置账号开始前设置" }).click();

  await expect(page.getByText("已重置账号的开始前设置")).toBeVisible();
  expect(resetCalled).toBe(true);
});

test("worksheet session review saves structured record", async ({ page }) => {
  await page.route(`${API}/worksheet/create`, async (route) => {
    await route.fulfill({ json: worksheetCreateResponse() });
  });
  await page.route(`${API}/users/demo_user/session-reviews`, async (route) => {
    const payload = route.request().postDataJSON() as {
      source: string;
      source_id: string;
      completed: string;
      anxiety_before: number;
      anxiety_after: number;
      next_step: string;
      save_record: boolean;
    };
    expect(payload.source).toBe("worksheet");
    expect(payload.source_id).toBe("worksheet_1");
    expect(payload.save_record).toBe(true);
    await route.fulfill({
      json: {
        review: {
          review_id: "review_1",
          user_id: "demo_user",
          source: payload.source,
          source_id: payload.source_id,
          completed: payload.completed,
          anxiety_before: payload.anxiety_before,
          anxiety_after: payload.anxiety_after,
          next_step_summary: payload.next_step,
          created_at: "2026-07-03T00:00:00Z"
        },
        saved: true,
        message: "已保存低敏结构化复盘。"
      }
    });
  });

  await page.goto("/worksheet");
  await page.getByRole("button", { name: "生成反思表" }).click();
  await page.getByRole("button", { name: "保存复盘" }).click();

  await expect(page.getByText("已保存低敏结构化复盘。")).toBeVisible();
});

test("roleplay consent approves and replays start action", async ({ page }) => {
  let startCalls = 0;
  await mockRoleplayConsent(page, () => {
    startCalls += 1;
    return startCalls;
  });

  await page.goto("/practice");
  await page.getByLabel("你想练习什么具体情境？").fill(
    "线上小组讨论中表达不同意见"
  );
  await page.getByRole("button", { name: "开始练习" }).click();
  await expect(page.getByText("需要同意").first()).toBeVisible();
  await page.getByRole("button", { name: "同意并继续" }).click();

  await expect(page.getByText("你好，我是同学。我们先从一个轻量回应开始。")).toBeVisible();
  expect(startCalls).toBe(2);
});

test("practice pause persists roleplay session status", async ({ page }) => {
  let pauseCalled = false;
  await page.route(`${API}/roleplay/start`, async (route) => {
    await route.fulfill({
      json: {
        session: roleplaySession(),
        opening_message: "你好，我是同学。我们先从一个轻量回应开始。"
      }
    });
  });
  await page.route(`${API}/roleplay/pause`, async (route) => {
    const payload = route.request().postDataJSON() as {
      session_id: string;
      user_id: string;
    };
    expect(payload.session_id).toBe("session_1");
    expect(payload.user_id).toBe("demo_user");
    pauseCalled = true;
    await route.fulfill({
      json: {
        session: { ...roleplaySession(), status: "paused" },
        message: "已保存角色扮演暂停状态。"
      }
    });
  });

  await page.goto("/practice");
  await page.getByLabel("你想练习什么具体情境？").fill(
    "和同学沟通小组分工"
  );
  await page.getByRole("button", { name: "开始练习" }).click();
  await page.getByRole("button", { name: "暂停练习" }).click();

  await expect(page.getByText("已保存角色扮演暂停状态。")).toBeVisible();
  await expect(page.getByText("已暂停", { exact: true }).first()).toBeVisible();
  expect(pauseCalled).toBe(true);
});

test("history shows paused roleplay session status", async ({ page }) => {
  await mockProfile(page);
  await page.route(`${API}/users/demo_user/intervention-plans?limit=20`, async (route) => {
    await route.fulfill({ json: { user_id: "demo_user", plans: [] } });
  });
  await page.route(`${API}/users/demo_user/session-reviews?limit=10`, async (route) => {
    await route.fulfill({ json: { user_id: "demo_user", reviews: [] } });
  });
  await page.route(`${API}/roleplay?user_id=demo_user&limit=10`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        sessions: [{ ...roleplaySession(), status: "paused" }]
      }
    });
  });

  await page.goto("/history");

  await expect(page.getByRole("heading", { name: "最近角色扮演" })).toBeVisible();
  await expect(page.getByText("线上小组讨论中表达不同意见")).toBeVisible();
  await expect(page.getByText("已暂停", { exact: true })).toBeVisible();
});

test("completed roleplay session restores feedback from history link", async ({ page }) => {
  await page.route(`${API}/roleplay/session_1?user_id=demo_user`, async (route) => {
    await route.fulfill({
      json: {
        session: {
          ...roleplaySessionWithUserTurn(),
          status: "completed"
        },
        opening_message: "你好，我是同学。我们先从一个轻量回应开始。"
      }
    });
  });
  await page.route(`${API}/roleplay/feedback`, async (route) => {
    const payload = route.request().postDataJSON() as {
      session_id: string;
      user_id: string;
    };
    expect(payload.session_id).toBe("session_1");
    expect(payload.user_id).toBe("demo_user");
    await route.fulfill({
      json: {
        session: {
          ...roleplaySessionWithUserTurn(),
          status: "completed"
        },
        feedback: roleplayFeedback()
      }
    });
  });

  await page.goto("/practice?session_id=session_1");

  await expect(page.getByText("已完成角色扮演")).toBeVisible();
  await expect(page.getByRole("button", { name: "查看反馈" })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "练习反馈" })).toBeVisible();
  await expect(page.getByText("清晰 4/5")).toBeVisible();
  await expect(page.getByText("先表达观点，再补一句理由。")).toBeVisible();
});

test("paused roleplay session resumes and completes feedback flow", async ({ page }) => {
  let resumeCalled = false;
  let messageCalled = false;
  let feedbackCalled = false;
  await page.route(`${API}/roleplay/session_1?user_id=demo_user`, async (route) => {
    await route.fulfill({
      json: {
        session: { ...roleplaySession(), status: "paused" },
        opening_message: "你好，我是同学。我们先从一个轻量回应开始。"
      }
    });
  });
  await page.route(`${API}/roleplay/resume`, async (route) => {
    const payload = route.request().postDataJSON() as {
      session_id: string;
      user_id: string;
    };
    expect(payload.session_id).toBe("session_1");
    expect(payload.user_id).toBe("demo_user");
    resumeCalled = true;
    await route.fulfill({
      json: {
        session: roleplaySession(),
        message: "已恢复角色扮演会话。"
      }
    });
  });
  await page.route(`${API}/roleplay/message`, async (route) => {
    const payload = route.request().postDataJSON() as {
      session_id: string;
      user_id: string;
      message: string;
    };
    expect(payload.session_id).toBe("session_1");
    expect(payload.user_id).toBe("demo_user");
    expect(payload.message).toContain("核心观点");
    messageCalled = true;
    await route.fulfill({
      json: {
        session: roleplaySessionWithUserTurn(),
        response: "这个表达已经比较清楚了，我们可以继续收紧理由。",
        safety_result: safety("low")
      }
    });
  });
  await page.route(`${API}/roleplay/feedback`, async (route) => {
    const payload = route.request().postDataJSON() as {
      session_id: string;
      user_id: string;
    };
    expect(payload.session_id).toBe("session_1");
    expect(payload.user_id).toBe("demo_user");
    feedbackCalled = true;
    await route.fulfill({
      json: {
        session: {
          ...roleplaySessionWithUserTurn(),
          status: "completed"
        },
        feedback: roleplayFeedback()
      }
    });
  });

  await page.goto("/practice?session_id=session_1");

  await expect(page.getByText("已暂停", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "获取反馈" })).toBeDisabled();
  await page.getByRole("button", { name: "继续练习" }).click();
  await expect(page.getByRole("button", { name: "发送一轮" })).toBeEnabled();
  await page.getByRole("button", { name: "发送一轮" }).click();
  await expect(page.getByRole("button", { name: "获取反馈" })).toBeEnabled();
  await page.getByRole("button", { name: "获取反馈" }).click();

  await expect(page.getByRole("heading", { name: "练习反馈" })).toBeVisible();
  await expect(page.getByText("清晰 4/5")).toBeVisible();
  expect(resumeCalled).toBe(true);
  expect(messageCalled).toBe(true);
  expect(feedbackCalled).toBe(true);
});

test("exposure consent approves and creates ladder", async ({ page }) => {
  let planCalls = 0;
  await mockProfile(page);
  await page.route(`${API}/users/demo_user/exposure`, async (route) => {
    await route.fulfill({ json: { user_id: "demo_user", plan: null } });
  });
  await page.route(`${API}/exposure/plan`, async (route) => {
    planCalls += 1;
    if (planCalls === 1) {
      await route.fulfill({
        status: 409,
        json: { detail: consentDetail("protocol_exposure_1", "create_exposure_plan") }
      });
      return;
    }
    await route.fulfill({
      json: {
        plan: exposurePlan(),
        intervention_plan_id: "intervention_exposure_1",
        intervention_plan: {
          ...interventionPlan(),
          plan_id: "intervention_exposure_1",
          session_id: "plan_1"
        },
        safety_result: safety("low"),
        blocked: false,
        response: "已创建社交练习阶梯。"
      }
    });
  });
  await page.route(`${API}/protocols/protocol_exposure_1/respond`, async (route) => {
    await route.fulfill({ json: protocol("protocol_exposure_1", "approved") });
  });

  await page.goto("/progress");
  await page.getByRole("button", { name: "创建阶梯" }).click();
  await expect(page.getByText("需要同意")).toBeVisible();
  await page.getByRole("button", { name: "同意并继续" }).click();

  await expect(
    page.getByRole("button", { name: /给同学发一句问候/ })
  ).toBeVisible();
  expect(planCalls).toBe(2);
});

test("linked exposure intervention plan restores full ladder detail", async ({ page }) => {
  const linkedPlan = linkedExposureInterventionPlan();
  await page.route(`${API}/intervention-plans/intervention_exposure_1?user_id=demo_user`, async (route) => {
    await route.fulfill({ json: { plan: linkedPlan } });
  });
  await page.route(`${API}/exposure/plan_1?user_id=demo_user`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        plan: exposurePlan(),
        intervention_plan_id: linkedPlan.plan_id,
        intervention_plan: linkedPlan
      }
    });
  });

  await page.goto("/progress?plan_id=intervention_exposure_1");

  await expect(page.getByText("已恢复社交练习阶梯和关联计划状态。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "练习阶梯" })).toBeVisible();
  await expect(page.getByRole("button", { name: /给同学发一句问候/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务反馈" })).toBeVisible();
});

test("direct exposure plan pause persists intervention status", async ({ page }) => {
  await page.route(`${API}/users/demo_user/exposure`, async (route) => {
    await route.fulfill({ json: { user_id: "demo_user", plan: null } });
  });
  await page.route(`${API}/exposure/plan`, async (route) => {
    await route.fulfill({
      json: {
        plan: exposurePlan(),
        intervention_plan_id: "intervention_exposure_pause_1",
        intervention_plan: {
          ...interventionPlan(),
          plan_id: "intervention_exposure_pause_1",
          session_id: "plan_1"
        },
        safety_result: safety("low"),
        blocked: false,
        response: "已创建社交练习阶梯。"
      }
    });
  });
  await page.route(
    `${API}/intervention-plans/intervention_exposure_pause_1/pause?user_id=demo_user`,
    async (route) => {
      await route.fulfill({
        json: {
          plan: {
            ...interventionPlan(),
            plan_id: "intervention_exposure_pause_1",
            session_id: "plan_1",
            status: "paused"
          }
        }
      });
    }
  );

  await page.goto("/progress");
  await page.getByRole("button", { name: "创建阶梯" }).click();
  await page.getByRole("button", { name: "暂停练习" }).click();

  await expect(page.getByText("已保存暂停状态。")).toBeVisible();
  await expect(page.getByText("已暂停").first()).toBeVisible();
});

test("progress pauses restored intervention plan through backend", async ({ page }) => {
  let pauseCalled = false;
  await page.route(`${API}/intervention-plans/intervention_1?user_id=demo_user`, async (route) => {
    await route.fulfill({ json: { plan: interventionPlan() } });
  });
  await page.route(`${API}/intervention-plans/intervention_1/pause?user_id=demo_user`, async (route) => {
    pauseCalled = true;
    await route.fulfill({
      json: {
        plan: {
          ...interventionPlan(),
          status: "paused",
          timeline: interventionPlan().timeline.map((step) => ({
            ...step,
            status: step.status === "completed" ? "completed" : "cancelled",
            result_summary:
              step.status === "completed" ? step.result_summary : "User paused practice."
          }))
        }
      }
    });
  });

  await page.goto("/progress?plan_id=intervention_1");
  await expect(page.getByText("已恢复从对话中创建的练习计划状态。")).toBeVisible();
  await page.getByRole("button", { name: "暂停练习" }).click();

  await expect(page.getByText("已保存暂停状态。")).toBeVisible();
  await expect(page.getByText("已暂停", { exact: true }).first()).toBeVisible();
  expect(pauseCalled).toBe(true);
});

test("privacy settings can export and delete memory", async ({ page }) => {
  await mockProfile(page);
  await page.route(`${API}/users/demo_user/memory/export`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        profile: userProfile(),
        records: {
          traces: [{ run_id: "run_1", input: "[minimized]" }],
          worksheets: []
        }
      }
    });
  });
  await page.route(`${API}/users/demo_user/memory`, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    await route.fulfill({
      json: {
        user_id: "demo_user",
        deleted_counts: { traces: 1, worksheets: 0 },
        profile_after_delete: userProfile()
      }
    });
  });

  await page.goto("/settings");
  await page.getByRole("button", { name: "导出" }).click();
  await expect(page.getByText("已载入导出内容")).toBeVisible();
  await expect(page.getByText("traces", { exact: true })).toBeVisible();

  await page.getByRole("textbox", { name: "DELETE", exact: true }).fill("DELETE");
  await page.getByRole("button", { name: "删除记忆" }).click();
  await expect(page.getByText("已删除当前用户的 1 条已保存记录。")).toBeVisible();
});

test("practice summary personalization consent can be revoked", async ({ page }) => {
  await mockProfile(page);
  await page.route(
    `${API}/users/demo_user/memory/consent/practice-summary`,
    async (route) => {
      expect(route.request().method()).toBe("PUT");
      expect(route.request().postDataJSON()).toEqual({
        consent_to_practice_summary: false
      });
      await route.fulfill({
        json: {
          user_id: "demo_user",
          consent_state: {
            store_conversation_history: true,
            consent_to_practice_summary: false,
            consent_to_save_preferences: false,
            do_not_store_raw_messages: true,
            allow_sensitive_memory: false
          }
        }
      });
    }
  );

  await page.goto("/settings");
  await page.getByRole("button", { name: "停止用于未来个性化" }).click();

  await expect(
    page.getByText("已停止在未来练习中使用历史练习摘要；原有练习记录仍保留。")
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "允许用于未来个性化" })
  ).toBeVisible();
});

test("memory center separates agent memory and supports archive control", async ({
  page
}) => {
  let archived = false;
  const memory = {
    memory_id: "memory_center_1",
    memory_type: "helpful_strategy",
    summary: "先写下一个关键词，再尝试表达观点。",
    scenario_type: "group_discussion",
    source_type: "user_confirmed",
    evidence_type: "user_confirmed",
    confidence: 1,
    status: "active",
    saved_reason: "user_confirmed_proposal",
    occurred_at: "2026-07-26T08:00:00Z",
    created_at: "2026-07-26T08:00:00Z",
    updated_at: "2026-07-26T08:00:00Z",
    last_retrieved_at: null,
    expires_at: "2027-07-26T08:00:00Z",
    version: 1
  };
  const doctor = {
    user_id: "demo_user",
    policy_version: "memory-doctor-v1",
    generated_at: "2026-07-26T08:30:00Z",
    scanned_counts: {
      episodic_memories: 1,
      thread_checkpoints: 0,
      pending_proposals: 0
    },
    thresholds: {
      stale_memory_days: 180,
      stale_checkpoint_days: 180,
      pending_proposal_days: 7,
      active_memory_token_budget: 512,
      conflict_term_overlap: 2
    },
    checks: [
      {
        code: "stale_unused_memory",
        status: "issues_found",
        issue_count: 1,
        detail_code: null
      },
      {
        code: "orphan_embedding",
        status: "not_applicable",
        issue_count: 0,
        detail_code: "embedding_index_disabled"
      }
    ],
    issues: [
      {
        issue_id: "1234567890abcdef",
        code: "stale_unused_memory",
        severity: "info",
        subject_type: "episodic_memory",
        subject_id_hashes: ["fedcba0987654321"],
        affected_count: 1,
        metadata: { unused_days: 190 },
        recommendation_code: "consider_archiving_stale_memory"
      }
    ],
    issues_truncated: false,
    auto_fix_applied: false,
    contains_memory_content: false
  };
  await page.route(`${API}/users/demo_user/memories`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        stable_memory: {
          consent_state: {
            store_conversation_history: true,
            consent_to_practice_summary: true,
            consent_to_save_preferences: false,
            do_not_store_raw_messages: true,
            allow_sensitive_memory: false
          },
          practice_preferences: {
            preferred_roleplay_difficulty: null,
            preferred_feedback_style: null,
            preferred_practice_scenarios: []
          },
          onboarding_profile: emptyOnboardingProfile(),
          disabled_memory_types: []
        },
        active_threads: [],
        memories: [
          {
            ...memory,
            status: archived ? "archived" : "active",
            version: archived ? 2 : 1
          }
        ],
        pending_proposals: [],
        doctor,
        memory_history_distinction:
          "Agent Memory 是经授权后用于未来个性化的摘要；聊天历史是原会话记录。"
      }
    });
  });
  await page.route(
    `${API}/users/demo_user/memories/memory_center_1/archive`,
    async (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().postDataJSON()).toEqual({ expected_version: 1 });
      archived = true;
      await route.fulfill({
        json: {
          user_id: "demo_user",
          memory: { ...memory, status: "archived", version: 2 },
          deleted: false
        }
      });
    }
  );

  await page.goto("/memory");

  await expect(page.getByRole("heading", { name: "记忆中心" })).toBeVisible();
  await expect(page.getByText("Agent Memory 与聊天历史")).toBeVisible();
  await expect(page.getByText("Memory Doctor（只读检查）")).toBeVisible();
  await expect(page.getByText("有长期未使用的记忆")).toBeVisible();
  await expect(page.getByText(/向量索引完整性：当前未启用/)).toBeVisible();
  await expect(page.getByText("fedcba0987654321")).toHaveCount(0);
  await expect(page.locator("textarea").first()).toHaveValue(memory.summary);
  await page.getByRole("button", { name: "归档" }).click();
  await expect(
    page.getByText("已归档；普通检索将不再使用这条记忆。")
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "恢复" })).toBeVisible();
});

test("cross-user denied surfaces as a retryable error", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("socialease.demoUserId", "other_user");
  });
  await page.route(`${API}/users/other_user/profile`, async (route) => {
    await route.fulfill({ status: 403, json: { detail: "cross-user access denied" } });
  });

  await page.goto("/settings");

  await expect(page.getByText("cross-user access denied")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
});

test("crisis chat flow shows safety-first response", async ({ page }) => {
  const now = "2026-07-27T08:00:00Z";
  const conversation = unifiedConversation(now);
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "socialease:history-notice:2026-07-01",
      "acknowledged"
    );
  });
  await page.route("**/api/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/conversations" && request.method() === "GET") {
      await route.fulfill({ json: { items: [conversation], next_cursor: null } });
      return;
    }
    if (path === "/api/conversations/conversation_1") {
      await route.fulfill({
        json: {
          conversation,
          events: { items: [], next_cursor: null },
          active_module_stack: [],
          pending_module_proposals: []
        }
      });
      return;
    }
    if (path.endsWith("/messages")) {
      const appended = [
        conversationEvent("crisis_user", 1, "user_message", "user", "我不想活了"),
        conversationEvent(
          "crisis_response",
          2,
          "crisis_escalated",
          "assistant",
          "我很担心你的安全。请现在联系可信任的人、学校心理中心或当地紧急服务。"
        )
      ];
      await route.fulfill({
        json: {
          conversation,
          appended_events: appended,
          active_module_stack: [],
          pending_module_proposal: null,
          response: appended[1].content,
          safety_result: safety("crisis"),
          context_diagnostics: conversationDiagnostics()
        }
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "unmocked route" } });
  });

  await page.goto("/chat");
  await page.locator("textarea").fill("我不想活了");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(page.getByText("风险: crisis")).toHaveCount(0);
  await expect(page.getByText("请现在联系可信任的人")).toBeVisible();
});

test("support query retry replays the failed request", async ({ page }) => {
  let calls = 0;
  await page.route(`${API}/support/query`, async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 503, json: { detail: "temporary backend outage" } });
      return;
    }
    await route.fulfill({
      json: {
        answer: "可以先联系学校公开支持资源页面中列出的服务。",
        citations: [
          {
            title: "Public support resource",
            source_name: "Synthetic public resource",
            source_type: "external_public",
            source_url: null,
            snippet: "Use verified public resource pages instead of invented phone numbers."
          }
        ],
        unknown: false,
        confidence: 0.8,
        retrieval: null,
        safety_result: safety("low"),
        blocked: false
      }
    });
  });

  await page.goto("/support");
  await page.getByRole("button", { name: "查询" }).click();
  await expect(page.getByText("temporary backend outage")).toBeVisible();
  await page.getByRole("button", { name: "重试" }).click();

  await expect(page.getByText("可以先联系学校公开支持资源页面中列出的服务。")).toBeVisible();
  expect(calls).toBe(2);
});

test("default product pages hide developer diagnostics", async ({ page }) => {
  await page.route(`${API}/support/query`, async (route) => {
    await route.fulfill({
      json: {
        answer: "回答基于已收录的公开资源。",
        citations: [],
        unknown: false,
        confidence: 0.8,
        retrieval: null,
        safety_result: safety("low"),
        blocked: false
      }
    });
  });
  await page.route(`${API}/worksheet/create`, async (route) => {
    await route.fulfill({ json: worksheetCreateResponse() });
  });
  await page.route(`${API}/roleplay/start`, async (route) => {
    await route.fulfill({
      json: {
        session: roleplaySession(),
        opening_message: "你好，我是同学。我们先从一个轻量回应开始。"
      }
    });
  });

  await page.goto("/support");
  await page.getByRole("button", { name: "查询" }).click();
  await expect(page.getByText("已完成安全检查")).toBeVisible();
  await expect(page.getByText("风险", { exact: false })).toHaveCount(0);
  await expect(page.getByText("LLM", { exact: false })).toHaveCount(0);

  await page.goto("/worksheet");
  await page.getByRole("button", { name: "生成反思表" }).click();
  await expect(page.getByText("已生成反思表")).toBeVisible();
  await expect(page.getByText("low", { exact: true })).toHaveCount(0);
  await expect(page.getByText("LLM", { exact: false })).toHaveCount(0);

  await page.goto("/practice");
  await page.getByLabel("你想练习什么具体情境？").fill(
    "线上小组讨论中表达不同意见"
  );
  await page.getByRole("button", { name: "开始练习" }).click();
  await expect(page.getByText("基于练习资料")).toBeVisible();
  await expect(page.getByText("session_1", { exact: false })).toHaveCount(0);
  await expect(page.getByText("classroom_speech", { exact: false })).toHaveCount(0);
  await expect(page.getByText("LLM", { exact: false })).toHaveCount(0);
});

function unifiedConversation(now: string) {
  return {
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
}

function conversationEvent(
  eventId: string,
  sequenceNo: number,
  eventType: string,
  role: string,
  content: unknown,
  structuredPayload: Record<string, unknown> | null = null
) {
  return {
    event_id: eventId,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    sequence_no: sequenceNo,
    event_type: eventType,
    role,
    content,
    structured_payload: structuredPayload,
    module_run_id: null,
    parent_module_run_id: null,
    idempotency_key: `idempotency-${eventId}`,
    created_at: "2026-07-27T08:00:00Z"
  };
}

function moduleProposal(
  proposalId: string,
  moduleType: string,
  now: string
) {
  return {
    proposal_id: proposalId,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    proposed_module: moduleType,
    reason_code: "user_requested",
    bounded_parameters: {},
    status: "pending",
    request_hash: "a".repeat(64),
    expires_at: "2026-07-27T09:00:00Z",
    created_at: now
  };
}

function moduleRun(
  moduleRunId: string,
  moduleType: string,
  depth: number,
  parentModuleRunId: string | null,
  now: string
) {
  return {
    module_run_id: moduleRunId,
    conversation_id: "conversation_1",
    user_id: "demo_user",
    module_type: moduleType,
    parent_module_run_id: parentModuleRunId,
    depth,
    status: "active",
    module_parameters: {},
    domain_session_id: null,
    started_at: now,
    ended_at: null,
    version: 1
  };
}

function conversationDiagnostics() {
  return {
    conversation_id_hash: "hashed-conversation",
    recent_event_count: 0,
    recent_event_sequence_start: null,
    recent_event_sequence_end: null,
    compact_summary_version: null,
    active_module_count: 0,
    selected_memory_count: 0,
    estimated_tokens: 0,
    total_token_budget: 4096,
    dropped_sections: [],
    tokenizer_backend: "mock"
  };
}

async function mockProfile(page: Page) {
  await page.route(`${API}/users/demo_user/profile`, async (route) => {
    await route.fulfill({ json: userProfile() });
  });
}

async function mockDashboard(page: Page) {
  await mockProfile(page);
  await page.route(`${API}/users/demo_user/onboarding`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        onboarding_profile: {
          ...emptyOnboardingProfile(),
          preferred_scenario: "classroom_speech",
          current_anxiety_level: 6,
          boundary_acknowledged: true
        }
      }
    });
  });
  await page.route(`${API}/users/demo_user/intervention-plans?limit=5`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        plans: [interventionPlan()]
      }
    });
  });
  await page.route(`${API}/users/demo_user/session-reviews?limit=5`, async (route) => {
    await route.fulfill({
      json: {
        user_id: "demo_user",
        reviews: [sessionReview()]
      }
    });
  });
}

async function mockRoleplayConsent(page: Page, nextStartCall: () => number) {
  await page.route(`${API}/roleplay/start`, async (route) => {
    const call = nextStartCall();
    if (call === 1) {
      await route.fulfill({
        status: 409,
        json: { detail: consentDetail("protocol_roleplay_1", "start_roleplay") }
      });
      return;
    }
    await route.fulfill({
      json: {
        session: roleplaySession(),
        opening_message: "你好，我是同学。我们先从一个轻量回应开始。"
      }
    });
  });
  await page.route(`${API}/protocols/protocol_roleplay_1/respond`, async (route) => {
    await route.fulfill({ json: protocol("protocol_roleplay_1", "approved") });
  });
}

function userProfile() {
  return {
    user_id: "demo_user",
    practice_summary: {
      recent_scenarios: [],
      roleplay_session_count: 1,
      worksheet_count: 1,
      exposure_attempt_count: 0,
      latest_anxiety_level: null,
      preferred_difficulty: null
    },
    consent_state: {
      store_conversation_history: true,
      consent_to_practice_summary: true,
      consent_to_save_preferences: false,
      do_not_store_raw_messages: true,
      allow_sensitive_memory: false
    },
    practice_preferences: {
      preferred_roleplay_difficulty: null,
      preferred_feedback_style: "",
      preferred_practice_scenarios: []
    },
    privacy_notice: "仅保存轻量练习状态和低敏感度偏好。",
    memory_export_available: true,
    memory_delete_available: true
  };
}

function safety(riskLevel: string) {
  return {
    risk_level: riskLevel,
    reason: "mock safety result",
    llm_usage: { used: false, fallback_used: false }
  };
}

function trace(runId: string, riskLevel: string, intent: string) {
  return {
    run_id: runId,
    user_id: "demo_user",
    input: "[minimized]",
    safety_result: safety(riskLevel),
    intent_result: {
      intent,
      confidence: 1,
      reason: "mock router result",
      llm_usage: { used: false, fallback_used: false }
    },
    selected_agent: intent,
    output: "mock output",
    product_safe: true,
    privacy_summary: {
      trace_layer: "product_safe",
      raw_input_retained: false,
      raw_output_retained: false,
      fields: []
    },
    latency_ms: 1,
    errors: [],
    created_at: "2026-07-03T00:00:00Z"
  };
}

function consentDetail(protocolId: string, action: string) {
  return {
    action: "consent_required",
    consent_required: true,
    protocol_id: protocolId,
    protocol_status: "pending",
    protocol_expires_at: "2026-07-03T01:00:00Z",
    protocol_request_hash: `${protocolId}_hash`,
    harness_action: action
  };
}

function protocol(protocolId: string, status: string) {
  return {
    protocol: {
      protocol_id: protocolId,
      user_id: "demo_user",
      protocol_type: "consent_request",
      status,
      payload: {},
      created_at: "2026-07-03T00:00:00Z",
      updated_at: "2026-07-03T00:00:00Z"
    }
  };
}

function roleplaySession() {
  return {
    session_id: "session_1",
    user_id: "demo_user",
    scenario: null,
    scenario_spec: {
      scenario_id: "scenario_1",
      safe_summary: "线上小组讨论中表达不同意见",
      practice_goal: "清楚表达观点并保持尊重",
      counterpart_role: "group",
      interaction_mode: "express_view",
      skill_codes: ["disagreement", "assertive_expression"],
      context_tags: ["group"]
    },
    difficulty: 2,
    status: "active",
    messages: [
      {
        role: "agent",
        content: "你好，我是同学。我们先从一个轻量回应开始。",
        created_at: "2026-07-03T00:00:00Z"
      }
    ],
    retrieved_guidance: {
      query: "小组讨论 表达不同意见",
      answer: "demo guidance",
      citations: [],
      unknown: false,
      confidence: 0.8,
      no_guidance_found: false
    },
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z"
  };
}

function roleplaySessionWithUserTurn() {
  return {
    ...roleplaySession(),
    messages: [
      ...roleplaySession().messages,
      {
        role: "user",
        content: "我想先表达核心观点，再补充一个例子。",
        created_at: "2026-07-03T00:01:00Z"
      },
      {
        role: "agent",
        content: "这个表达已经比较清楚了，我们可以继续收紧理由。",
        created_at: "2026-07-03T00:02:00Z"
      }
    ]
  };
}

function roleplayFeedback() {
  return {
    clarity_score: 4,
    naturalness_score: 4,
    assertiveness_score: 3,
    empathy_score: 3,
    rubric_breakdown: [
      {
        dimension: "clarity",
        score: 4,
        signals: [
          { name: "main_point", label: "有核心观点", present: true, weight: 1 },
          { name: "specific_reason", label: "理由具体", present: true, weight: 1 }
        ],
        rationale: "表达有主线，能让对方理解你的意思。"
      }
    ],
    strengths: ["先表达观点，再补一句理由。"],
    suggestions: ["可以把请求说得再具体一点。"],
    next_try_prompt: "下一轮尝试用一句话表达观点，再用一句话说明原因。",
    citations: []
  };
}

function exposurePlan() {
  return {
    plan_id: "plan_1",
    user_id: "demo_user",
    target_scenario: "课堂发言",
    current_anxiety_level: 7,
    previous_attempts: [],
    tasks: [
      {
        task_id: "task_1",
        title: "给同学发一句问候",
        description: "选择一位熟悉同学，发送一句低压力问候。",
        difficulty: 2,
        estimated_time_minutes: 5,
        success_criteria: "完成一句问候即可。",
        fallback_task: "先写一句问候草稿。",
        citations: []
      }
    ],
    attempts: [],
    recommended_next_task_id: "task_1",
    disclaimer: "这是社交练习计划，不是治疗方案。",
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:00:00Z"
  };
}

function linkedExposureInterventionPlan() {
  return {
    ...interventionPlan(),
    plan_id: "intervention_exposure_1",
    session_id: "plan_1",
    timeline: interventionPlan().timeline.map((step, index) => ({
      ...step,
      skill: index === 0 ? "exposure_planning_skill" : step.skill,
      title: index === 0 ? "创建社交练习阶梯" : step.title
    }))
  };
}

function interventionPlan() {
  return {
    plan_id: "intervention_1",
    user_id: "demo_user",
    session_id: "session_1",
    status: "active",
    protocol_id: null,
    current_step_id: "step_1",
    completed_steps: 0,
    total_steps: 2,
    progress_ratio: 0,
    timeline: [
      {
        order: 1,
        step_id: "step_1",
        title: "课堂发言开场练习",
        status: "in_progress",
        skill: "roleplay_practice",
        intensity: 2,
        requires_consent: true,
        protocol_id: "protocol_1",
        stop_condition: "用户暂停或感到太难时停止。",
        result_summary: null,
        is_current: true
      },
      {
        order: 2,
        step_id: "step_2",
        title: "保存一次简短复盘",
        status: "pending",
        skill: "progress_review",
        intensity: 1,
        requires_consent: false,
        protocol_id: null,
        stop_condition: null,
        result_summary: null,
        is_current: false
      }
    ],
    created_at: "2026-07-03T00:00:00Z",
    updated_at: "2026-07-03T00:05:00Z"
  };
}

function sessionReview() {
  return {
    review_id: "review_1",
    user_id: "demo_user",
    source: "roleplay",
    source_id: "session_1",
    completed: "completed",
    anxiety_before: 7,
    anxiety_after: 5,
    next_step_summary: "下次先练一句短开场。",
    created_at: "2026-07-03T00:08:00Z"
  };
}

function emptyOnboardingProfile() {
  return {
    primary_goal: null,
    preferred_scenario: null,
    current_anxiety_level: null,
    practice_preference: null,
    wants_pause_reminders: true,
    wants_auto_review: true,
    boundary_acknowledged: false
  };
}

function worksheetCreateResponse() {
  return {
    worksheet: {
      worksheet_id: "worksheet_1",
      user_id: "demo_user",
      source_message: "[minimized]",
      fields: {
        situation: "课堂发言",
        automatic_thought: "我会说错",
        emotion: "焦虑",
        emotion_intensity: 7,
        evidence_for: "之前卡过壳",
        evidence_against: "也有表达清楚的时候",
        alternative_thought: "可以先说核心观点",
        next_action: "练习开场两遍"
      },
      citations: [],
      disclaimer: "这是自助反思练习，不是治疗。",
      missing_fields: [],
      gentle_followup_questions: [],
      created_at: "2026-07-03T00:00:00Z"
    },
    safety_result: safety("low"),
    missing_fields: [],
    gentle_followup_questions: [],
    disclaimer: "这是自助反思练习，不是治疗。",
    blocked: false,
    response: "已生成结构化反思表。",
    llm_usage: { used: false, fallback_used: false }
  };
}

async function fulfillChatStream(
  route: Route,
  response: Record<string, unknown>
): Promise<void> {
  const runId = String(response.run_id ?? "test_run");
  const progress = [
    "safety",
    "routing",
    "skill",
    "output_guardrail",
    "trace"
  ]
    .map(
      (stage, index) =>
        `event: progress\ndata: ${JSON.stringify({
          type: "stage_completed",
          run_id: runId,
          stage,
          stage_latency_ms: 5,
          elapsed_ms: (index + 1) * 5
        })}\n\n`
    )
    .join("");
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: { "Cache-Control": "no-cache" },
    body: `${progress}event: final\ndata: ${JSON.stringify(
      response
    )}\n\nevent: done\ndata: {}\n\n`
  });
}
