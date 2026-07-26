import type {
  AccountDeleteResponse,
  AuthConfigResponse,
  AuthMeResponse,
  AuthResponse,
  ChatProgressEvent,
  ChatResponse,
  ConsentRequiredDetail,
  ExposureCompleteResponse,
  ExposurePlanResponse,
  InterventionPlanListResponse,
  InterventionPlanResponse,
  ProtocolResponse,
  MemoryPreferencesUpdateResponse,
  PracticePreferences,
  PracticeSummaryConsentUpdateResponse,
  SessionReviewCompletion,
  SessionReviewCreateResponse,
  SessionReviewListResponse,
  SessionReviewSource,
  RoleplayFeedbackResponse,
  RoleplayMessageResponse,
  RoleplayPauseResponse,
  RoleplayResumeResponse,
  RoleplayScenario,
  RoleplaySessionListResponse,
  RoleplayStartResponse,
  LogoutResponse,
  SupportQueryResponse,
  TraceRecord,
  UserExposureResponse,
  UserMemoryDeleteResponse,
  UserMemoryExportResponse,
  UserOnboardingProfile,
  UserOnboardingProfileResponse,
  UserProfileResponse,
  WorksheetCreateResponse,
  WorksheetRecord
} from "./types";
import {
  authHeaders,
  clearAccountSession,
  csrfToken,
  currentUserId,
  getAuthState,
  saveAccountSession,
  tokenStorageMode
} from "./auth";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const PROTOCOL_HEADER_NAME = "X-SocialEase-Protocol-Id";

export type ChatStreamHandlers = {
  onProgress?: (event: ChatProgressEvent) => void;
};

export class ConsentRequiredError extends Error {
  detail: ConsentRequiredDetail;

  constructor(detail: ConsentRequiredDetail) {
    super("Consent required");
    this.name = "ConsentRequiredError";
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  retryOnUnauthorized = true
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...csrfHeaders(),
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    if (
      response.status === 401 &&
      retryOnUnauthorized &&
      !path.startsWith("/api/auth/")
    ) {
      const refreshed = await tryRefreshSession();
      if (refreshed) {
        return request<T>(path, init, false);
      }
    }
    const parsed = await parseApiError(response);
    if (parsed.consentRequired) {
      throw new ConsentRequiredError(parsed.consentRequired);
    }
    throw new Error(parsed.message);
  }

  return response.json() as Promise<T>;
}

async function tryRefreshSession(): Promise<boolean> {
  const state = getAuthState();
  const refreshToken =
    tokenStorageMode() === "localStorage" ? state.refreshToken : "";
  const response = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: refreshToken
      ? JSON.stringify({ refresh_token: refreshToken })
      : undefined
  });
  if (!response.ok) {
    clearAccountSession();
    return false;
  }
  const payload = (await response.json()) as AuthResponse;
  saveAccountSession({
    userId: payload.user.user_id,
    email: payload.user.email,
    accessToken: payload.tokens.access_token,
    refreshToken: payload.tokens.refresh_token
  });
  return true;
}

async function parseApiError(
  response: Response
): Promise<{ message: string; consentRequired?: ConsentRequiredDetail }> {
  const fallback = `Request failed with ${response.status}`;
  const text = await response.text();
  if (!text) {
    return { message: fallback };
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (response.status === 409 && isConsentRequiredDetail(payload.detail)) {
      return {
        message: "Consent required",
        consentRequired: payload.detail
      };
    }
    if (typeof payload.detail === "string") {
      return { message: payload.detail };
    }
    if (Array.isArray(payload.detail)) {
      return {
        message: payload.detail
          .map((item) =>
            typeof item === "object" && item !== null && "msg" in item
              ? String(item.msg)
              : JSON.stringify(item)
          )
          .join("; ")
      };
    }
  } catch {
    return { message: text };
  }
  return { message: text || fallback };
}

async function requestChatStream(
  userId: string,
  message: string,
  context: Record<string, unknown>,
  handlers: ChatStreamHandlers,
  retryOnUnauthorized = true
): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
      ...csrfHeaders()
    },
    body: JSON.stringify({ user_id: userId, message, context })
  });

  if (response.status === 401 && retryOnUnauthorized) {
    const refreshed = await tryRefreshSession();
    if (refreshed) {
      return requestChatStream(userId, message, context, handlers, false);
    }
  }
  if (!response.ok) {
    const parsed = await parseApiError(response);
    throw new Error(parsed.message);
  }
  if (!response.body) {
    throw new Error("当前浏览器无法读取流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: ChatResponse | null = null;
  let streamError: string | null = null;

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseSseBlock(block);
      if (event) {
        if (event.name === "progress") {
          handlers.onProgress?.(event.payload as ChatProgressEvent);
        } else if (event.name === "final") {
          finalResponse = event.payload as ChatResponse;
        } else if (event.name === "error") {
          const payload = event.payload as { message?: unknown };
          streamError =
            typeof payload.message === "string"
              ? payload.message
              : "Agent 工作流未能生成安全回复。";
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) {
      break;
    }
  }

  if (streamError) {
    throw new Error(streamError);
  }
  if (!finalResponse) {
    throw new Error("连接已结束，但没有收到最终回复。");
  }
  return finalResponse;
}

function parseSseBlock(
  block: string
): { name: string; payload: unknown } | null {
  let name = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      name = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).trimStart());
    }
  }
  if (data.length === 0) {
    return null;
  }
  return { name, payload: JSON.parse(data.join("\n")) as unknown };
}

function isConsentRequiredDetail(value: unknown): value is ConsentRequiredDetail {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const item = value as Record<string, unknown>;
  return (
    item.action === "consent_required" &&
    item.consent_required === true &&
    typeof item.protocol_id === "string" &&
    typeof item.protocol_request_hash === "string" &&
    typeof item.harness_action === "string"
  );
}

function protocolHeaders(protocolId?: string): Record<string, string> {
  return protocolId ? { [PROTOCOL_HEADER_NAME]: protocolId } : {};
}

function csrfHeaders(): Record<string, string> {
  const token = csrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}

export const api = {
  authConfig() {
    return request<AuthConfigResponse>("/api/auth/config");
  },

  authMe() {
    return request<AuthMeResponse>("/api/auth/me");
  },

  register(email: string, password: string, inviteCode?: string) {
    return request<AuthResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        ...(inviteCode ? { invite_code: inviteCode } : {})
      })
    });
  },

  login(email: string, password: string) {
    return request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password })
    });
  },

  refresh(refreshToken?: string) {
    const token = tokenStorageMode() === "localStorage" ? refreshToken : undefined;
    return request<AuthResponse>("/api/auth/refresh", {
      method: "POST",
      body: token ? JSON.stringify({ refresh_token: token }) : undefined
    });
  },

  logout(refreshToken?: string) {
    const token = tokenStorageMode() === "localStorage" ? refreshToken : undefined;
    return request<LogoutResponse>("/api/auth/logout", {
      method: "POST",
      body: token ? JSON.stringify({ refresh_token: token }) : undefined
    });
  },

  deleteAccount() {
    return request<AccountDeleteResponse>("/api/auth/account", {
      method: "DELETE"
    });
  },

  chat(userId: string, message: string, context: Record<string, unknown> = {}) {
    return request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        message,
        context
      })
    });
  },

  chatStream(
    userId: string,
    message: string,
    context: Record<string, unknown> = {},
    handlers: ChatStreamHandlers = {}
  ) {
    return requestChatStream(userId, message, context, handlers);
  },

  respondToProtocol(protocolId: string, userId: string, approved: boolean) {
    return request<ProtocolResponse>(
      `/api/protocols/${encodeURIComponent(protocolId)}/respond`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          approved
        })
      }
    );
  },

  getRun(runId: string) {
    return request<TraceRecord>(`/api/runs/${encodeURIComponent(runId)}`);
  },

  getInterventionPlan(planId: string, userId: string) {
    const params = new URLSearchParams({ user_id: userId });
    return request<InterventionPlanResponse>(
      `/api/intervention-plans/${encodeURIComponent(planId)}?${params.toString()}`
    );
  },

  listInterventionPlans(userId: string, limit = 20) {
    const params = new URLSearchParams({ limit: String(limit) });
    return request<InterventionPlanListResponse>(
      `/api/users/${encodeURIComponent(userId)}/intervention-plans?${params.toString()}`
    );
  },

  querySupportResources(query: string, searchSessionId?: string | null) {
    return request<SupportQueryResponse>("/api/support/query", {
      method: "POST",
      body: JSON.stringify({
        query,
        user_id: currentUserId(),
        search_session_id: searchSessionId ?? null
      })
    });
  },

  startRoleplay(
    userId: string,
    scenario: RoleplayScenario,
    difficulty: number,
    options: { protocolId?: string } = {}
  ) {
    return request<RoleplayStartResponse>("/api/roleplay/start", {
      method: "POST",
      headers: protocolHeaders(options.protocolId),
      body: JSON.stringify({
        user_id: userId,
        scenario,
        difficulty
      })
    });
  },

  getRoleplaySession(sessionId: string, userId: string) {
    const params = new URLSearchParams({ user_id: userId });
    return request<RoleplayStartResponse>(
      `/api/roleplay/${encodeURIComponent(sessionId)}?${params.toString()}`
    );
  },

  listRoleplaySessions(userId: string, limit = 10) {
    const params = new URLSearchParams({ user_id: userId, limit: String(limit) });
    return request<RoleplaySessionListResponse>(
      `/api/roleplay?${params.toString()}`
    );
  },

  sendRoleplayMessage(sessionId: string, userId: string, message: string) {
    return request<RoleplayMessageResponse>("/api/roleplay/message", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        user_id: userId,
        message
      })
    });
  },

  getRoleplayFeedback(sessionId: string, userId: string) {
    return request<RoleplayFeedbackResponse>("/api/roleplay/feedback", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        user_id: userId
      })
    });
  },

  pauseRoleplaySession(sessionId: string, userId: string) {
    return request<RoleplayPauseResponse>("/api/roleplay/pause", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        user_id: userId
      })
    });
  },

  resumeRoleplaySession(sessionId: string, userId: string) {
    return request<RoleplayResumeResponse>("/api/roleplay/resume", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        user_id: userId
      })
    });
  },

  createWorksheet(userId: string, message: string) {
    return request<WorksheetCreateResponse>("/api/worksheet/create", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        message
      })
    });
  },

  getWorksheet(worksheetId: string) {
    return request<WorksheetRecord>(
      `/api/worksheet/${encodeURIComponent(worksheetId)}`
    );
  },

  supplementWorksheet(worksheetId: string, userId: string, message: string) {
    return request<WorksheetCreateResponse>("/api/worksheet/supplement", {
      method: "POST",
      body: JSON.stringify({
        worksheet_id: worksheetId,
        user_id: userId,
        message
      })
    });
  },

  createExposurePlan(
    userId: string,
    targetScenario: string,
    currentAnxietyLevel: number,
    previousAttempts: string[],
    options: { protocolId?: string } = {}
  ) {
    return request<ExposurePlanResponse>("/api/exposure/plan", {
      method: "POST",
      headers: protocolHeaders(options.protocolId),
      body: JSON.stringify({
        user_id: userId,
        target_scenario: targetScenario,
        current_anxiety_level: currentAnxietyLevel,
        previous_attempts: previousAttempts
      })
    });
  },

  completeExposureTask(
    userId: string,
    taskId: string,
    status: "completed" | "skipped" | "too_hard",
    anxietyBefore: number,
    anxietyAfter: number,
    reflection: string,
    options: { protocolId?: string } = {}
  ) {
    return request<ExposureCompleteResponse>("/api/exposure/complete", {
      method: "POST",
      headers: protocolHeaders(options.protocolId),
      body: JSON.stringify({
        user_id: userId,
        task_id: taskId,
        status,
        anxiety_before: anxietyBefore,
        anxiety_after: anxietyAfter,
        reflection
      })
    });
  },

  getUserExposure(userId: string) {
    return request<UserExposureResponse>(
      `/api/users/${encodeURIComponent(userId)}/exposure`
    );
  },

  getExposurePlan(planId: string, userId: string) {
    const params = new URLSearchParams({ user_id: userId });
    return request<UserExposureResponse>(
      `/api/exposure/${encodeURIComponent(planId)}?${params.toString()}`
    );
  },

  getUserProfile(userId: string) {
    return request<UserProfileResponse>(
      `/api/users/${encodeURIComponent(userId)}/profile`
    );
  },

  getOnboardingProfile(userId: string) {
    return request<UserOnboardingProfileResponse>(
      `/api/users/${encodeURIComponent(userId)}/onboarding`
    );
  },

  updateOnboardingProfile(userId: string, profile: UserOnboardingProfile) {
    return request<UserOnboardingProfileResponse>(
      `/api/users/${encodeURIComponent(userId)}/onboarding`,
      {
        method: "PUT",
        body: JSON.stringify({ onboarding_profile: profile })
      }
    );
  },

  resetOnboardingProfile(userId: string) {
    return request<UserOnboardingProfileResponse>(
      `/api/users/${encodeURIComponent(userId)}/onboarding`,
      { method: "DELETE" }
    );
  },

  exportUserMemory(userId: string) {
    return request<UserMemoryExportResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory/export`
    );
  },

  deleteUserMemory(userId: string) {
    return request<UserMemoryDeleteResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory`,
      { method: "DELETE" }
    );
  },

  updateMemoryPreferences(
    userId: string,
    preferences: PracticePreferences,
    options: { protocolId?: string } = {}
  ) {
    return request<MemoryPreferencesUpdateResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory/preferences`,
      {
        method: "PUT",
        headers: protocolHeaders(options.protocolId),
        body: JSON.stringify({
          consent_to_save_preferences: true,
          practice_preferences: preferences
        })
      }
    );
  },

  disableMemoryPreferences(userId: string) {
    return request<MemoryPreferencesUpdateResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory/preferences`,
      { method: "DELETE" }
    );
  },

  updatePracticeSummaryConsent(userId: string, enabled: boolean) {
    return request<PracticeSummaryConsentUpdateResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory/consent/practice-summary`,
      {
        method: "PUT",
        body: JSON.stringify({ consent_to_practice_summary: enabled })
      }
    );
  },

  createSessionReview(
    userId: string,
    payload: {
      source: SessionReviewSource;
      sourceId?: string | null;
      completed: SessionReviewCompletion;
      anxietyBefore: number;
      anxietyAfter: number;
      nextStep: string;
      saveRecord: boolean;
    }
  ) {
    return request<SessionReviewCreateResponse>(
      `/api/users/${encodeURIComponent(userId)}/session-reviews`,
      {
        method: "POST",
        body: JSON.stringify({
          source: payload.source,
          source_id: payload.sourceId ?? null,
          completed: payload.completed,
          anxiety_before: payload.anxietyBefore,
          anxiety_after: payload.anxietyAfter,
          next_step: payload.nextStep,
          save_record: payload.saveRecord
        })
      }
    );
  },

  listSessionReviews(userId: string, limit = 20) {
    const params = new URLSearchParams({ limit: String(limit) });
    return request<SessionReviewListResponse>(
      `/api/users/${encodeURIComponent(userId)}/session-reviews?${params.toString()}`
    );
  },

  pauseInterventionPlan(planId: string, userId: string) {
    const params = new URLSearchParams({ user_id: userId });
    return request<InterventionPlanResponse>(
      `/api/intervention-plans/${encodeURIComponent(planId)}/pause?${params.toString()}`,
      { method: "POST" }
    );
  }
};
