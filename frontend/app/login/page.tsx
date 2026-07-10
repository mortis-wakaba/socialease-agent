"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { frontendSignupEnabled, saveAccountSession } from "@/lib/auth";
import {
  Button,
  ErrorBox,
  FormHint,
  PageHeader,
  Panel,
  TextInput
} from "@/components/ui";

type AuthMode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryAuth, setRetryAuth] = useState<AuthRetry | null>(null);
  const [signupEnabled, setSignupEnabled] = useState(frontendSignupEnabled());

  useEffect(() => {
    let active = true;
    api.authConfig()
      .then((config) => {
        if (active) {
          setSignupEnabled(config.signup_enabled);
        }
      })
      .catch(() => {
        if (active) {
          setSignupEnabled(frontendSignupEnabled());
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitAuth(mode, email.trim(), password, inviteCode.trim());
  }

  async function submitAuth(
    authMode: AuthMode,
    authEmail: string,
    authPassword: string,
    authInviteCode = ""
  ) {
    setLoading(true);
    setError(null);
    setRetryAuth(null);
    if (authMode === "register" && !signupEnabled && !authInviteCode) {
      setError("当前试点关闭公开注册。请输入邀请码，或使用已有账号登录。");
      setLoading(false);
      return;
    }
    try {
      const response =
        authMode === "login"
          ? await api.login(authEmail, authPassword)
          : await api.register(authEmail, authPassword, authInviteCode);
      saveAccountSession({
        userId: response.user.user_id,
        email: response.user.email,
        accessToken: response.tokens.access_token,
        refreshToken: response.tokens.refresh_token
      });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "认证失败");
      setRetryAuth({
        mode: authMode,
        email: authEmail,
        password: authPassword,
        inviteCode: authInviteCode
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title={mode === "login" ? "登录" : "注册"}
        description="使用试点账号验证多用户边界。这个账号系统不代表医疗服务关系。"
      />
      <div className="max-w-xl">
        <Panel title={mode === "login" ? "账号登录" : "创建账号"}>
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block text-sm font-medium text-slate-700">
              邮箱
              <TextInput
                className="mt-1"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                autoComplete="email"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              密码
              <TextInput
                className="mt-1"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                minLength={mode === "register" ? 8 : 1}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </label>
            {mode === "register" ? (
              <label className="block text-sm font-medium text-slate-700">
                邀请码
                <TextInput
                  className="mt-1"
                  type="text"
                  value={inviteCode}
                  onChange={(event) => setInviteCode(event.target.value)}
                  autoComplete="one-time-code"
                  placeholder={signupEnabled ? "可选" : "试点注册需要邀请码"}
                  required={!signupEnabled}
                />
              </label>
            ) : null}
            <ErrorBox
              message={error}
              onRetry={
                retryAuth
                  ? () =>
                      void submitAuth(
                        retryAuth.mode,
                        retryAuth.email,
                        retryAuth.password,
                        retryAuth.inviteCode
                      )
                  : undefined
              }
              retrying={loading}
              retryLabel="重试认证"
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={loading}>
                {loading
                  ? "处理中..."
                  : mode === "login"
                    ? "登录"
                    : "注册"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setMode(mode === "login" ? "register" : "login");
                  setError(null);
                }}
              >
                {mode === "login"
                  ? signupEnabled
                    ? "创建账号"
                    : "邀请码注册"
                  : "使用已有账号"}
              </Button>
            </div>
            <FormHint>
              {signupEnabled
                ? "你可以使用账号登录。邀请码可用于封闭试点。"
                : "当前试点关闭公开注册。已有账号可直接登录，新用户需要试点组织者发放的邀请码。"}
            </FormHint>
          </form>
        </Panel>
      </div>
    </>
  );
}

type AuthRetry = {
  mode: AuthMode;
  email: string;
  password: string;
  inviteCode: string;
};
