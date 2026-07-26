import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:8000/api";

const protectedPages = [
  "/chat",
  "/practice",
  "/progress",
  "/worksheet",
  "/onboarding",
  "/dashboard",
  "/history",
  "/memory",
  "/settings",
  "/trace"
];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.clear();
  });
});

test("production auth environment probe is production mode", async ({ page }) => {
  await page.route(`${API}/auth/config`, async (route) => {
    await route.fulfill({
      json: {
        auth_mode: "production",
        signup_enabled: false,
        cookie_auth_enabled: false
      }
    });
  });

  await page.goto("/login");

  await expect(page.getByRole("heading", { name: "账号登录" })).toBeVisible();
  await expect(page.getByRole("button", { name: "邀请码注册" })).toBeVisible();
  await page.getByRole("button", { name: "邀请码注册" }).click();
  await expect(page.getByLabel("邀请码")).toBeVisible();
});

for (const path of protectedPages) {
  test(`production mode redirects unauthenticated ${path} to login`, async ({
    page
  }) => {
    const businessCalls = await captureBusinessApiCalls(page);

    await page.goto(path);

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "账号登录" })).toBeVisible();
    expect(businessCalls()).toEqual([]);
  });
}

test("production mode keeps public pages accessible", async ({ page }) => {
  await page.goto("/privacy");

  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.getByRole("heading", { name: "隐私和数据说明" })).toBeVisible();
});

test("production mode hides trace navigation from normal users", async ({ page }) => {
  await loginAs(page, ["user"]);
  await page.route(`${API}/auth/me`, async (route) => {
    await route.fulfill({
      json: authMe(["user"], false)
    });
  });

  await page.goto("/dashboard");

  await expect(page.getByRole("link", { name: "Trace" })).toHaveCount(0);
});

test("production mode blocks direct trace page for normal users", async ({ page }) => {
  await loginAs(page, ["user"]);
  const traceCalls = await captureTraceDetailCalls(page);
  await page.route(`${API}/auth/me`, async (route) => {
    await route.fulfill({
      json: authMe(["user"], false)
    });
  });

  await page.goto("/trace");

  await expect(page).toHaveURL(/\/trace$/);
  await expect(page.getByText("需要开发者权限")).toBeVisible();
  await expect(page.getByText("Safety")).toHaveCount(0);
  await expect(page.getByText("Router")).toHaveCount(0);
  expect(traceCalls()).toEqual([]);
});

test("production mode clears a stale local marker and redirects trace to login", async ({
  page
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("socialease.accountUserId", "stale_user");
    window.localStorage.setItem("socialease.accountEmail", "stale@example.com");
  });
  const traceCalls = await captureTraceDetailCalls(page);
  await page.route(`${API}/auth/me`, async (route) => {
    await route.fulfill({
      json: {
        authenticated: false,
        user_id: null,
        roles: [],
        auth_mode: "production",
        is_demo_user: false,
        developer_endpoints_enabled: true,
        developer_access: false
      }
    });
  });

  await page.goto("/trace");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "账号登录" })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => ({
        userId: window.localStorage.getItem("socialease.accountUserId"),
        email: window.localStorage.getItem("socialease.accountEmail")
      }))
    )
    .toEqual({ userId: null, email: null });
  expect(traceCalls()).toEqual([]);
});

test("production mode shows trace navigation to developer users", async ({ page }) => {
  await loginAs(page, ["user", "developer"]);
  await page.route(`${API}/auth/me`, async (route) => {
    await route.fulfill({
      json: authMe(["user", "developer"], true)
    });
  });

  await page.goto("/dashboard");

  await expect(page.getByRole("link", { name: "Trace" })).toBeVisible();
});

test("production mode refreshes trace navigation after developer login", async ({
  page
}) => {
  let loggedIn = false;
  await page.route(`${API}/auth/config`, async (route) => {
    await route.fulfill({
      json: {
        auth_mode: "production",
        signup_enabled: false,
        cookie_auth_enabled: false
      }
    });
  });
  await page.route(`${API}/auth/me`, async (route) => {
    if (!loggedIn) {
      await route.fulfill({ status: 401, json: { detail: "Not authenticated" } });
      return;
    }
    await route.fulfill({
      json: authMe(["user", "developer"], true)
    });
  });
  await page.route(`${API}/auth/login`, async (route) => {
    loggedIn = true;
    await route.fulfill({
      json: authResponse(["user", "developer"])
    });
  });

  await page.goto("/login");

  await expect(page.getByRole("link", { name: "Trace" })).toHaveCount(0);
  await page.getByLabel("邮箱").fill("developer@example.com");
  await page.getByLabel("密码").fill("password");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("link", { name: "Trace" })).toBeVisible();
});

test("production mode allows developer users to open trace page", async ({ page }) => {
  await loginAs(page, ["user", "developer"]);
  await page.route(`${API}/auth/me`, async (route) => {
    await route.fulfill({
      json: authMe(["user", "developer"], true)
    });
  });

  await page.goto("/trace");

  await expect(page.getByRole("heading", { name: "Trace" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Load Trace" })).toBeVisible();
});

async function loginAs(page: Page, roles: string[]) {
  await page.addInitScript((nextRoles) => {
    window.localStorage.setItem("socialease.bearerToken", `token-${nextRoles.join("-")}`);
    window.localStorage.setItem("socialease.accountUserId", "production_user");
    window.localStorage.setItem("socialease.accountEmail", "pilot@example.com");
  }, roles);
}

async function captureBusinessApiCalls(page: Page) {
  const calls: string[] = [];
  await page.route(/^http:\/\/127\.0\.0\.1:8000\/api\/(?!auth\/).*/, async (route) => {
    calls.push(route.request().url());
    await route.fulfill({ status: 500, json: { detail: "unexpected business API call" } });
  });
  return () => calls;
}

async function captureTraceDetailCalls(page: Page) {
  const calls: string[] = [];
  await page.route(/^http:\/\/127\.0\.0\.1:8000\/api\/runs\/.*/, async (route) => {
    calls.push(route.request().url());
    await route.fulfill({ status: 500, json: { detail: "unexpected trace call" } });
  });
  await page.route(/^http:\/\/127\.0\.0\.1:8000\/api\/intervention-plans\/.*/, async (route) => {
    calls.push(route.request().url());
    await route.fulfill({ status: 500, json: { detail: "unexpected trace plan call" } });
  });
  return () => calls;
}

function authMe(roles: string[], developerAccess: boolean) {
  return {
    authenticated: true,
    user_id: "production_user",
    roles,
    auth_mode: "production",
    is_demo_user: false,
    developer_endpoints_enabled: true,
    developer_access: developerAccess
  };
}

function authResponse(roles: string[]) {
  return {
    user: {
      user_id: "production_user",
      email: "developer@example.com",
      roles
    },
    tokens: {
      access_token: `token-${roles.join("-")}`,
      refresh_token: `refresh-${roles.join("-")}`,
      token_type: "bearer",
      expires_in: 3600
    }
  };
}
