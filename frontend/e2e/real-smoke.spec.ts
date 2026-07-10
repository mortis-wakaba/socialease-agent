import { expect, test } from "@playwright/test";

test("real backend user flow: onboarding, pause, review, export, account delete", async ({
  page
}) => {
  const email = `smoke_${Date.now()}@example.com`;
  const password = "correct-horse-password";
  const sensitivePhone = "13912345678";

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
  await page.getByPlaceholder("输入一个社交压力场景...").fill("我想模拟课堂发言");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("需要同意").first()).toBeVisible();
  await page.getByRole("button", { name: "同意并继续" }).click();
  await expect(page.getByText("角色扮演已开始")).toBeVisible();
  await expect(page.getByRole("link", { name: "查看 Trace" })).toHaveCount(0);

  await page.getByRole("button", { name: "暂停练习" }).click();
  await expect(page.getByText("已保存暂停状态。")).toBeVisible();

  await page.getByRole("link", { name: "打开练习" }).click();
  await expect(page).toHaveURL(/\/practice\?session_id=/);
  await expect(page.getByRole("heading", { name: "角色扮演练习" })).toBeVisible();
  await page.getByRole("button", { name: "暂停练习" }).click();
  await expect(page.getByText("已保存角色扮演暂停状态。")).toBeVisible();
  await expect(page.getByRole("button", { name: "获取反馈" })).toBeDisabled();
  await page.getByRole("button", { name: "继续练习" }).click();
  await expect(page.getByText("已恢复练习")).toBeVisible();
  await page
    .getByPlaceholder("输入你的练习回复...")
    .fill("我想先说一个核心观点，因为这样能让同学更容易理解。");
  await page.getByRole("button", { name: "发送一轮" }).click();
  await expect(page.getByText("已完成一轮练习")).toBeVisible();
  await page.getByRole("button", { name: "获取反馈" }).click();
  await expect(page.getByText("练习反馈")).toBeVisible();
  await page
    .getByLabel("下一步")
    .fill(`下次先练一句短开场，手机号 ${sensitivePhone}`);
  await page.getByRole("button", { name: "保存复盘" }).click();
  await expect(page.getByText("已保存低敏结构化复盘")).toBeVisible();

  await page.goto("/dashboard");
  await expect(page.getByText("已暂停").first()).toBeVisible();
  await expect(page.getByText("下次先练一句短开场")).toBeVisible();
  await expect(page.getByText(sensitivePhone)).toHaveCount(0);

  await page.goto("/history");
  await expect(page.getByRole("heading", { name: "练习历史" })).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();

  await page.goto("/settings");
  await page.getByRole("button", { name: "导出" }).click();
  await expect(page.getByText("已载入导出内容")).toBeVisible();
  await expect(page.getByText("[redacted:phone]")).toBeVisible();
  await expect(page.getByText(sensitivePhone)).toHaveCount(0);

  await page.getByRole("textbox", { name: "DELETE ACCOUNT" }).fill("DELETE ACCOUNT");
  await page.getByRole("button", { name: "删除账号" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/history");
  await expect(page).toHaveURL(/\/login$/);
});
