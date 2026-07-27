import { expect, test } from "@playwright/test";

test("real backend user flow: unified conversation, module, history, account delete", async ({
  page
}) => {
  const email = `smoke_${Date.now()}@example.com`;
  const password = "correct-horse-password";
  const roleplayRequest = "我想模拟课堂发言";
  const roleplayTurn = "我想先说一个核心观点，让同学更容易理解。";

  await page.goto("/login");
  await page.getByRole("button", { name: /创建账号|邀请码注册/ }).click();
  await page.getByLabel("邮箱").fill(email);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "注册" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText(email)).toBeVisible();
  const storedTokens = await page.evaluate(() => ({
    access: window.localStorage.getItem("socialease.bearerToken"),
    refresh: window.localStorage.getItem("socialease.refreshToken")
  }));
  expect(storedTokens).toEqual({ access: null, refresh: null });

  await page.goto("/onboarding");
  await page.getByLabel(/保存低敏练习偏好/).uncheck();
  await page.getByLabel(/我了解 SocialEase/).check();
  await page.getByRole("button", { name: "完成设置" }).click();
  await expect(page.getByText("长期练习偏好没有保存")).toBeVisible();

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "练习工作台" })).toBeVisible();
  await expect(page.getByText("已确认边界")).toBeVisible();

  await page.goto("/chat");
  await expect(page.getByRole("heading", { name: "统一对话" })).toBeVisible();
  await page.getByRole("button", { name: "新建对话" }).click();
  await page.getByLabel("我已了解上述保存、导出和删除方式。").check();
  await page.getByRole("button", { name: "了解并新建" }).click();
  await expect(page.getByText("开始这段对话")).toBeVisible();

  const conversationInput = page.getByPlaceholder(
    "描述一个社交压力场景或继续普通对话…"
  );
  await conversationInput.fill(roleplayRequest);
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("是否进入角色扮演？")).toBeVisible();
  await page.getByRole("button", { name: "确认进入" }).click();
  await expect(page.getByText("已进入 roleplay 模块。")).toBeVisible();
  await expect(page.getByPlaceholder("继续 角色扮演…")).toBeVisible();
  await expect(page.getByRole("link", { name: "查看 Trace" })).toHaveCount(0);

  await page.getByPlaceholder("继续 角色扮演…").fill(roleplayTurn);
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(roleplayTurn)).toBeVisible();
  await expect(page.getByText("SocialEase · 模块").last()).toBeVisible();

  await page.getByRole("button", { name: "结束当前模块" }).click();
  await expect(page.getByText("用户已结束当前模块。")).toBeVisible();
  await expect(conversationInput).toBeVisible();

  await page.reload();
  await expect(page.getByText(roleplayRequest, { exact: true })).toBeVisible();
  await expect(page.getByText(roleplayTurn, { exact: true })).toBeVisible();
  await expect(page.getByText(`旧练习 · ${roleplayRequest}`)).toHaveCount(0);
  await expect(page.getByText("用户已结束当前模块。")).toBeVisible();

  await page.goto("/settings");
  await page.getByRole("textbox", { name: "DELETE ACCOUNT" }).fill("DELETE ACCOUNT");
  await page.getByRole("button", { name: "删除账号" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});
