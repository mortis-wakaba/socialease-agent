"use client";

const DEMO_USER_KEY = "socialease.demoUserId";
const BEARER_TOKEN_KEY = "socialease.bearerToken";
const REFRESH_TOKEN_KEY = "socialease.refreshToken";
const ACCOUNT_USER_ID_KEY = "socialease.accountUserId";
const ACCOUNT_EMAIL_KEY = "socialease.accountEmail";
const AUTH_CHANGED_EVENT = "socialease-auth-changed";

export const DEFAULT_DEMO_USER_ID = "demo_user";

export function frontendAuthMode(): "demo" | "production" {
  return process.env.NEXT_PUBLIC_SOCIALEASE_AUTH_MODE === "production"
    ? "production"
    : "demo";
}

export function frontendSignupEnabled(): boolean {
  return process.env.NEXT_PUBLIC_SOCIALEASE_ENABLE_SIGNUP !== "false";
}

export function tokenStorageMode(): "cookie" | "localStorage" {
  const configured = process.env.NEXT_PUBLIC_SOCIALEASE_TOKEN_STORAGE;
  if (configured === "localStorage") {
    return "localStorage";
  }
  if (configured === "cookie") {
    return "cookie";
  }
  return frontendAuthMode() === "production" ? "cookie" : "localStorage";
}

export type AuthState = {
  demoUserId: string;
  bearerToken: string;
  refreshToken: string;
  accountUserId: string;
  accountEmail: string;
  mode: "demo" | "bearer";
};

export function getAuthState(): AuthState {
  if (typeof window === "undefined") {
    return {
      demoUserId: DEFAULT_DEMO_USER_ID,
      bearerToken: "",
      refreshToken: "",
      accountUserId: "",
      accountEmail: "",
      mode: "demo"
    };
  }
  const demoUserId =
    window.localStorage.getItem(DEMO_USER_KEY)?.trim() || DEFAULT_DEMO_USER_ID;
  const bearerToken = window.localStorage.getItem(BEARER_TOKEN_KEY)?.trim() || "";
  const refreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY)?.trim() || "";
  const accountUserId =
    window.localStorage.getItem(ACCOUNT_USER_ID_KEY)?.trim() || "";
  const accountEmail = window.localStorage.getItem(ACCOUNT_EMAIL_KEY)?.trim() || "";
  return {
    demoUserId,
    bearerToken,
    refreshToken,
    accountUserId,
    accountEmail,
    mode: bearerToken || accountUserId ? "bearer" : "demo"
  };
}

export function currentUserId(): string {
  const state = getAuthState();
  return state.accountUserId || state.demoUserId;
}

export function isAuthenticatedForFrontend(): boolean {
  const state = getAuthState();
  return (
    frontendAuthMode() !== "production" ||
    Boolean(state.bearerToken || state.accountUserId)
  );
}

export function authHeaders(): Record<string, string> {
  const state = getAuthState();
  if (state.accountUserId && tokenStorageMode() === "cookie") {
    return {};
  }
  if (tokenStorageMode() === "localStorage" && state.bearerToken) {
    return { Authorization: `Bearer ${state.bearerToken}` };
  }
  if (frontendAuthMode() === "production") {
    return {};
  }
  return { "X-Demo-User-Id": state.demoUserId };
}

export function saveAuthState(next: {
  demoUserId?: string;
  bearerToken?: string;
  refreshToken?: string;
  accountUserId?: string;
  accountEmail?: string;
}) {
  if (typeof window === "undefined") {
    return;
  }
  if (next.demoUserId !== undefined) {
    const value = next.demoUserId.trim() || DEFAULT_DEMO_USER_ID;
    window.localStorage.setItem(DEMO_USER_KEY, value);
  }
  if (next.bearerToken !== undefined) {
    setOrRemove(BEARER_TOKEN_KEY, next.bearerToken);
  }
  if (next.refreshToken !== undefined) {
    setOrRemove(REFRESH_TOKEN_KEY, next.refreshToken);
  }
  if (next.accountUserId !== undefined) {
    setOrRemove(ACCOUNT_USER_ID_KEY, next.accountUserId);
  }
  if (next.accountEmail !== undefined) {
    setOrRemove(ACCOUNT_EMAIL_KEY, next.accountEmail);
  }
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function saveAccountSession(next: {
  userId: string;
  email: string;
  accessToken: string;
  refreshToken: string;
}) {
  const storageMode = tokenStorageMode();
  saveAuthState({
    bearerToken: storageMode === "localStorage" ? next.accessToken : "",
    refreshToken: storageMode === "localStorage" ? next.refreshToken : "",
    accountUserId: next.userId,
    accountEmail: next.email
  });
}

export function csrfToken(): string {
  if (typeof document === "undefined") {
    return "";
  }
  return readCookie("socialease_csrf_token");
}

export function clearAccountSession() {
  saveAuthState({
    bearerToken: "",
    refreshToken: "",
    accountUserId: "",
    accountEmail: ""
  });
}

export function subscribeAuthState(listener: () => void): () => void {
  if (typeof window === "undefined") {
    return () => undefined;
  }
  window.addEventListener(AUTH_CHANGED_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(AUTH_CHANGED_EVENT, listener);
    window.removeEventListener("storage", listener);
  };
}

function setOrRemove(key: string, value: string) {
  const trimmed = value.trim();
  if (trimmed) {
    window.localStorage.setItem(key, trimmed);
  } else {
    window.localStorage.removeItem(key);
  }
}

function readCookie(name: string): string {
  const prefix = `${name}=`;
  return document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length) ?? "";
}
