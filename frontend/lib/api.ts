import type {
  AccountDeleteResponse,
  AuthConfigResponse,
  AuthMeResponse,
  AuthResponse,
  Conversation,
  ConversationDetailResponse,
  ConversationExportResponse,
  ConversationMessageResponse,
  ConversationPage,
  ConversationStatus,
  ModuleControlResponse,
  ConsentRequiredDetail,
  InterventionPlanListResponse,
  InterventionPlanResponse,
  ProtocolResponse,
  MemoryPreferencesUpdateResponse,
  MemoryCenterResponse,
  MemoryMutationResponse,
  MemoryProposalDecisionResponse,
  MemoryType,
  MemoryTypePersonalizationResponse,
  PracticePreferences,
  PracticeSummaryConsentUpdateResponse,
  SessionReviewCompletion,
  SessionReviewCreateResponse,
  SessionReviewListResponse,
  SessionReviewSource,
  RoleplaySessionListResponse,
  LogoutResponse,
  TraceRecord,
  UserExposureResponse,
  UserMemoryDeleteResponse,
  UserMemoryExportResponse,
  UserOnboardingProfile,
  UserOnboardingProfileResponse,
  UserProfileResponse,
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

  createConversation(userId: string, title = "新对话") {
    return request<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        title,
        history_notice_version: "2026-07-01",
        history_notice_acknowledged: true
      })
    });
  },

  listConversations(userId: string, limit = 100) {
    const params = new URLSearchParams({
      user_id: userId,
      limit: String(limit)
    });
    return request<ConversationPage>(
      `/api/conversations?${params.toString()}`
    );
  },

  getConversation(
    conversationId: string,
    userId: string,
    cursor?: string | null
  ) {
    const params = new URLSearchParams({
      user_id: userId,
      limit: "200"
    });
    if (cursor) {
      params.set("cursor", cursor);
    }
    return request<ConversationDetailResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}?${params.toString()}`
    );
  },

  sendConversationMessage(
    conversationId: string,
    userId: string,
    message: string,
    idempotencyKey: string
  ) {
    return request<ConversationMessageResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          message,
          idempotency_key: idempotencyKey
        })
      }
    );
  },

  acceptModuleProposal(
    conversationId: string,
    proposalId: string,
    userId: string,
    requestHash: string
  ) {
    return request<ModuleControlResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}/module-proposals/${encodeURIComponent(proposalId)}/accept`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          request_hash: requestHash
        })
      }
    );
  },

  rejectModuleProposal(
    conversationId: string,
    proposalId: string,
    userId: string,
    requestHash: string
  ) {
    return request(
      `/api/conversations/${encodeURIComponent(conversationId)}/module-proposals/${encodeURIComponent(proposalId)}/reject`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          request_hash: requestHash
        })
      }
    );
  },

  terminateModule(
    conversationId: string,
    moduleRunId: string,
    userId: string
  ) {
    return request<ModuleControlResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}/modules/${encodeURIComponent(moduleRunId)}/terminate`,
      {
        method: "POST",
        body: JSON.stringify({ user_id: userId })
      }
    );
  },

  terminateAllModules(conversationId: string, userId: string) {
    return request<ModuleControlResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}/modules/terminate-all`,
      {
        method: "POST",
        body: JSON.stringify({ user_id: userId })
      }
    );
  },

  updateConversation(
    conversationId: string,
    userId: string,
    expectedVersion: number,
    update: { title?: string; status?: ConversationStatus }
  ) {
    return request<Conversation>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          user_id: userId,
          expected_version: expectedVersion,
          ...update
        })
      }
    );
  },

  exportConversation(conversationId: string, userId: string) {
    const params = new URLSearchParams({ user_id: userId });
    return request<ConversationExportResponse>(
      `/api/conversations/${encodeURIComponent(conversationId)}/export?${params.toString()}`
    );
  },

  deleteConversation(conversationId: string, userId: string) {
    return request(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      {
        method: "DELETE",
        body: JSON.stringify({
          user_id: userId,
          confirm_delete: true
        })
      }
    );
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

  listRoleplaySessions(userId: string, limit = 10) {
    const params = new URLSearchParams({ user_id: userId, limit: String(limit) });
    return request<RoleplaySessionListResponse>(
      `/api/roleplay?${params.toString()}`
    );
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
      {
        method: "DELETE",
        body: JSON.stringify({ confirm_delete: true })
      }
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

  getMemoryCenter(userId: string) {
    return request<MemoryCenterResponse>(
      `/api/users/${encodeURIComponent(userId)}/memories`
    );
  },

  updateMemoryTypePersonalization(
    userId: string,
    memoryType: MemoryType,
    enabled: boolean
  ) {
    return request<MemoryTypePersonalizationResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory/personalization/${encodeURIComponent(memoryType)}`,
      {
        method: "PUT",
        body: JSON.stringify({ enabled })
      }
    );
  },

  editMemory(
    userId: string,
    memoryId: string,
    summary: string,
    expectedVersion: number
  ) {
    return request<MemoryMutationResponse>(
      `/api/users/${encodeURIComponent(userId)}/memories/${encodeURIComponent(memoryId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          summary,
          expected_version: expectedVersion
        })
      }
    );
  },

  archiveMemory(userId: string, memoryId: string, expectedVersion: number) {
    return memoryLifecycleRequest(
      userId,
      memoryId,
      "archive",
      expectedVersion
    );
  },

  restoreMemory(userId: string, memoryId: string, expectedVersion: number) {
    return memoryLifecycleRequest(
      userId,
      memoryId,
      "restore",
      expectedVersion
    );
  },

  deleteMemoryItem(userId: string, memoryId: string, expectedVersion: number) {
    return request<MemoryMutationResponse>(
      `/api/users/${encodeURIComponent(userId)}/memories/${encodeURIComponent(memoryId)}`,
      {
        method: "DELETE",
        body: JSON.stringify({ expected_version: expectedVersion })
      }
    );
  },

  decideMemoryProposal(
    userId: string,
    proposalId: string,
    decision: "confirm" | "reject",
    expectedVersion: number
  ) {
    return request<MemoryProposalDecisionResponse>(
      `/api/users/${encodeURIComponent(userId)}/memory-proposals/${encodeURIComponent(proposalId)}/${decision}`,
      {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion })
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

function memoryLifecycleRequest(
  userId: string,
  memoryId: string,
  action: "archive" | "restore",
  expectedVersion: number
) {
  return request<MemoryMutationResponse>(
    `/api/users/${encodeURIComponent(userId)}/memories/${encodeURIComponent(memoryId)}/${action}`,
    {
      method: "POST",
      body: JSON.stringify({ expected_version: expectedVersion })
    }
  );
}
