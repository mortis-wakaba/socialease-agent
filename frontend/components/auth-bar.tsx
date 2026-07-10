"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import {
  DEFAULT_DEMO_USER_ID,
  authHeaders,
  clearAccountSession,
  frontendAuthMode,
  getAuthState,
  saveAuthState,
  subscribeAuthState,
  tokenStorageMode
} from "@/lib/auth";
import { api } from "@/lib/api";
import { Badge, Button, TextInput } from "@/components/ui";

export function AuthBar() {
  const [demoUserId, setDemoUserId] = useState(DEFAULT_DEMO_USER_ID);
  const [bearerToken, setBearerToken] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [accountEmail, setAccountEmail] = useState("");
  const [accountUserId, setAccountUserId] = useState("");
  const [mode, setMode] = useState<"demo" | "bearer">("demo");
  const [message, setMessage] = useState<string | null>(null);
  const [devPanelOpen, setDevPanelOpen] = useState(false);
  const isProductionAuth = frontendAuthMode() === "production";
  const storageMode = tokenStorageMode();

  useEffect(() => {
    function refresh() {
      const state = getAuthState();
      setDemoUserId(state.demoUserId);
      setBearerToken(state.bearerToken);
      setRefreshToken(state.refreshToken);
      setAccountEmail(state.accountEmail);
      setAccountUserId(state.accountUserId);
      setMode(state.mode);
    }
    refresh();
    return subscribeAuthState(refresh);
  }, []);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveAuthState({ demoUserId, bearerToken });
    setMessage("已应用本地身份。");
  }

  async function logout() {
    if (mode === "bearer") {
      try {
        await api.logout(refreshToken || undefined);
        setMessage("已退出登录。");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "本地退出失败。");
      }
    }
    setBearerToken("");
    setRefreshToken("");
    setAccountEmail("");
    setAccountUserId("");
    clearAccountSession();
  }

  if (isProductionAuth) {
    return (
      <div className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
            <Badge tone="info">Production 模式</Badge>
            <Badge tone={mode === "bearer" ? "info" : "warn"}>
              {mode === "bearer" ? "已登录" : "需要登录"}
            </Badge>
            <Badge tone={storageMode === "cookie" ? "good" : "warn"}>
              {storageMode === "cookie" ? "Cookie 会话" : "临时 token 存储"}
            </Badge>
            <span>
              {mode === "bearer"
                ? accountEmail || accountUserId || "账号用户"
                : "登录后可访问历史、设置和个人数据。"}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {mode === "bearer" ? (
              <>
                <Link
                  href="/settings"
                  className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
                >
                  账号设置
                </Link>
                <Button type="button" variant="secondary" onClick={logout}>
                  退出
                </Button>
              </>
            ) : (
              <Link
                href="/login"
                className="rounded-md border border-brand bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-[#176052]"
              >
                登录
              </Link>
            )}
          </div>
        </div>
      </div>
    );
  }

  const headerKeys = Object.keys(authHeaders());

  return (
    <div className="border-b border-line bg-panel">
      <div className="mx-auto max-w-6xl px-4 py-3">
        <button
          type="button"
          onClick={() => setDevPanelOpen((value) => !value)}
          className="flex w-full flex-wrap items-center justify-between gap-3 text-left"
        >
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
            <Badge tone="neutral">本地演示模式</Badge>
            <Badge tone={mode === "bearer" ? "info" : "neutral"}>
              {mode === "bearer" ? "已登录账号" : "本地用户"}
            </Badge>
            <span>
              {mode === "bearer"
                ? `已登录：${accountEmail || accountUserId || "账号用户"}`
                : `当前本地用户：${demoUserId}`}
            </span>
            {headerKeys.map((key) => (
              <Badge key={key}>{key}</Badge>
            ))}
          </div>
          <span className="text-xs font-medium text-brand">
            {devPanelOpen ? "收起身份设置" : "展开身份设置"}
          </span>
        </button>
        {devPanelOpen ? (
          <form
            onSubmit={handleSubmit}
            className="mt-3 grid gap-2 sm:grid-cols-[180px_1fr_auto_auto]"
          >
            <label className="text-xs font-medium uppercase text-slate-500">
              本地用户
              <TextInput
                value={demoUserId}
                onChange={(event) => setDemoUserId(event.target.value)}
                className="mt-1"
              />
            </label>
            {mode === "bearer" ? (
              <div className="rounded-md border border-line bg-white px-3 py-2 text-sm text-slate-700">
                <div className="truncate">{accountEmail || accountUserId}</div>
                <div className="mt-1 text-xs text-slate-500">已认证账号</div>
              </div>
            ) : (
              <label className="text-xs font-medium uppercase text-slate-500">
                bearer token
                <TextInput
                  value={bearerToken}
                  onChange={(event) => setBearerToken(event.target.value)}
                  placeholder="可选账号 token"
                  className="mt-1"
                />
              </label>
            )}
            {mode === "bearer" ? (
              <Button type="button" variant="secondary" onClick={logout}>
                退出
              </Button>
            ) : (
              <Button type="submit" variant="secondary">
                应用
              </Button>
            )}
            <Link
              href="/login"
              className="rounded-md border border-brand bg-brand px-3 py-2 text-center text-sm font-medium text-white hover:bg-[#176052]"
            >
              登录
            </Link>
          </form>
        ) : null}
        {message ? (
          <p className="mt-2 text-xs leading-5 text-slate-500">{message}</p>
        ) : null}
      </div>
    </div>
  );
}
