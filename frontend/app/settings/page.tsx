"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ConsentRequiredError, api } from "@/lib/api";
import { clearAccountSession } from "@/lib/auth";
import { clearOnboardingState } from "@/lib/onboarding";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import { DirectConsentCard } from "@/components/direct-consent-card";
import type {
  ConsentRequiredDetail,
  PracticePreferences,
  UserMemoryExportResponse,
  UserProfileResponse
} from "@/lib/types";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  FormHint,
  PageHeader,
  Panel,
  Select
} from "@/components/ui";

const DEFAULT_PREFERENCES: PracticePreferences = {
  preferred_roleplay_difficulty: null,
  preferred_feedback_style: null,
  preferred_practice_scenarios: []
};

const FEEDBACK_STYLE_OPTIONS: Array<{
  value: NonNullable<PracticePreferences["preferred_feedback_style"]>;
  label: string;
}> = [
  { value: "gentle_specific", label: "温和、具体、可执行" },
  { value: "brief_actionable", label: "简短、行动导向" },
  { value: "encouraging_reflective", label: "鼓励式、带一点反思" }
];

export default function SettingsPage() {
  const auth = useRequireAuth();
  const userId = auth.userId;
  const [profile, setProfile] = useState<UserProfileResponse | null>(null);
  const [preferences, setPreferences] =
    useState<PracticePreferences>(DEFAULT_PREFERENCES);
  const [newScenarioPreference, setNewScenarioPreference] = useState("");
  const [exported, setExported] = useState<UserMemoryExportResponse | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [accountDeleteConfirm, setAccountDeleteConfirm] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [pendingConsent, setPendingConsent] =
    useState<PendingSettingsConsent | null>(null);
  const [approvingConsent, setApprovingConsent] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !userId) {
      return;
    }
    void loadProfile();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, userId]);

  const savedRecordCounts = useMemo(() => {
    if (!exported) {
      return [];
    }
    return Object.entries(exported.records).map(([name, rows]) => ({
      name,
      count: rows.length
    }));
  }, [exported]);

  async function loadProfile() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.getUserProfile(userId);
      setProfile(result);
      setPreferences({
        preferred_roleplay_difficulty:
          result.practice_preferences.preferred_roleplay_difficulty,
        preferred_feedback_style:
          result.practice_preferences.preferred_feedback_style,
        preferred_practice_scenarios:
          result.practice_preferences.preferred_practice_scenarios
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法载入设置");
      setRetryAction({
        label: "重试刷新",
        run: () => {
          void loadProfile();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function exportMemory() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      const result = await api.exportUserMemory(userId);
      setExported(result);
      setStatusMessage("已载入导出内容，可在右侧预览。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法导出记忆");
      setRetryAction({
        label: "重试导出",
        run: () => {
          void exportMemory();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function deleteMemory() {
    if (!userId) {
      return;
    }
    if (deleteConfirm !== "DELETE") {
      setError("请输入 DELETE 确认删除。");
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      const result = await api.deleteUserMemory(userId);
      setProfile(result.profile_after_delete);
      setExported(null);
      setDeleteConfirm("");
      setPreferences(DEFAULT_PREFERENCES);
      clearOnboardingState();
      setStatusMessage(
        `已删除当前用户的 ${sumCounts(result.deleted_counts)} 条已保存记录。`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法删除记忆");
      setRetryAction({
        label: "重试删除",
        run: () => {
          void deleteMemory();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function deleteAccount() {
    if (accountDeleteConfirm !== "DELETE ACCOUNT") {
      setError("请输入 DELETE ACCOUNT 确认删除账号。");
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      const result = await api.deleteAccount();
      clearAccountSession();
      setProfile(null);
      setExported(null);
      setAccountDeleteConfirm("");
      setStatusMessage(
        `账号已删除，并清理 ${sumCounts(result.deleted_memory_counts)} 条用户记录。`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法删除账号");
      setRetryAction({
        label: "重试删除账号",
        run: () => {
          void deleteAccount();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function disablePreferences() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      const result = await api.disableMemoryPreferences(userId);
      setPreferences(result.practice_preferences);
      await loadProfile();
      setStatusMessage("已关闭长期练习偏好。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法关闭长期偏好");
      setRetryAction({
        label: "重试关闭",
        run: () => {
          void disablePreferences();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function updatePracticeSummaryConsent(enabled: boolean) {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      const result = await api.updatePracticeSummaryConsent(userId, enabled);
      setProfile((current) =>
        current
          ? { ...current, consent_state: result.consent_state }
          : current
      );
      setStatusMessage(
        enabled
          ? "已允许在未来练习中使用历史练习摘要。"
          : "已停止在未来练习中使用历史练习摘要；原有练习记录仍保留。"
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法更新练习摘要授权");
      setRetryAction({
        label: "重试更新",
        run: () => {
          void updatePracticeSummaryConsent(enabled);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function resetOnboarding() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    try {
      await api.resetOnboardingProfile(userId);
      clearOnboardingState();
      setStatusMessage("已重置账号的开始前设置。下次进入对话会提示重新设置。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法重置开始前设置");
      setRetryAction({
        label: "重试重置",
        run: () => {
          void resetOnboarding();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function savePreferences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    setPendingConsent(null);
    const request: SavePreferencesRequest = {
      userId,
      preferences: normalizedPreferences(preferences)
    };
    try {
      await savePreferencesWithRequest(request);
    } catch (err) {
      if (err instanceof ConsentRequiredError) {
        setPendingConsent({ detail: err.detail, request });
        setStatusMessage("保存长期偏好前需要明确同意。");
      } else {
        setError(err instanceof Error ? err.message : "无法保存偏好");
        setRetryAction({
          label: "重试保存",
          run: () => {
            void savePreferencesWithRequest(request);
          }
        });
      }
    } finally {
      setLoading(false);
    }
  }

  async function savePreferencesWithRequest(
    request: SavePreferencesRequest,
    protocolId?: string
  ) {
    const result = await api.updateMemoryPreferences(
      request.userId,
      request.preferences,
      { protocolId }
    );
    setPreferences(result.practice_preferences);
    await loadProfile();
    setPendingConsent(null);
    setStatusMessage("已在明确同意后保存练习偏好。");
  }

  async function approvePendingConsent() {
    if (!pendingConsent) {
      return;
    }
    setApprovingConsent(true);
    setError(null);
    setRetryAction(null);
    try {
      await api.respondToProtocol(
        pendingConsent.detail.protocol_id,
        pendingConsent.request.userId,
        true
      );
      await savePreferencesWithRequest(
        pendingConsent.request,
        pendingConsent.detail.protocol_id
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法批准同意");
      setRetryAction({
        label: "重试同意",
        run: () => {
          void approvePendingConsent();
        }
      });
    } finally {
      setApprovingConsent(false);
    }
  }

  async function rejectPendingConsent() {
    if (!pendingConsent) {
      return;
    }
    setApprovingConsent(true);
    setError(null);
    setRetryAction(null);
    try {
      await api.respondToProtocol(
        pendingConsent.detail.protocol_id,
        pendingConsent.request.userId,
        false
      );
      setPendingConsent(null);
      setStatusMessage("已取消同意，偏好没有保存。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消同意");
      setRetryAction({
        label: "重试取消",
        run: () => {
          void rejectPendingConsent();
        }
      });
    } finally {
      setApprovingConsent(false);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="设置"
        description="查看隐私控制，导出或删除已保存的练习记录，并管理低敏感度的练习偏好。"
      />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Panel title="隐私控制">
            {profile ? (
              <div className="space-y-3 text-sm leading-6 text-slate-700">
                <div className="flex flex-wrap gap-2">
                  <Badge tone="info">{userId ?? "未登录"}</Badge>
                  <Badge tone={profile.consent_state.store_conversation_history ? "good" : "warn"}>
                    {profile.consent_state.store_conversation_history
                      ? "对话历史长期保存"
                      : "对话历史未保存"}
                  </Badge>
                  <Badge tone={profile.consent_state.do_not_store_raw_messages ? "good" : "warn"}>
                    Agent Memory 原始文本最小化
                  </Badge>
                  <Badge tone={profile.consent_state.consent_to_save_preferences ? "good" : "neutral"}>
                    偏好保存同意
                  </Badge>
                  <Badge tone={profile.consent_state.consent_to_practice_summary ? "good" : "neutral"}>
                    历史摘要个性化
                  </Badge>
                </div>
                <p>{profile.privacy_notice}</p>
                <div className="space-y-2 rounded-md border border-line bg-white p-3">
                  <p>
                    练习记录用于历史、进度和导出；下面的授权只控制这些记录的摘要
                    是否可以进入未来的 Agent 上下文。
                  </p>
                  <Button
                    type="button"
                    variant={
                      profile.consent_state.consent_to_practice_summary
                        ? "secondary"
                        : "primary"
                    }
                    disabled={loading}
                    aria-pressed={profile.consent_state.consent_to_practice_summary}
                    onClick={() =>
                      void updatePracticeSummaryConsent(
                        !profile.consent_state.consent_to_practice_summary
                      )
                    }
                  >
                    {profile.consent_state.consent_to_practice_summary
                      ? "停止用于未来个性化"
                      : "允许用于未来个性化"}
                  </Button>
                </div>
                <dl className="grid grid-cols-2 gap-2 rounded-md border border-line bg-white p-3">
                  <Metric label="Role-play sessions" value={profile.practice_summary.roleplay_session_count} />
                  <Metric label="Worksheets" value={profile.practice_summary.worksheet_count} />
                  <Metric label="Exposure attempts" value={profile.practice_summary.exposure_attempt_count} />
                  <Metric
                    label="Latest anxiety"
                    value={profile.practice_summary.latest_anxiety_level ?? "-"}
                  />
                </dl>
              </div>
            ) : (
              <EmptyState title="尚未载入资料" description="请选择本地用户或登录账号，然后刷新设置。" />
            )}
          </Panel>

          <Panel title="已保存的数据">
            <div className="space-y-3 text-sm text-slate-700">
              <p>
                已保存记录可能包含流程追踪、角色扮演会话、反思表、社交练习计划、
                同意协议、练习步骤状态和记忆设置。
              </p>
              <p>
                原始社交或心理相关文本默认最小化保存。敏感标识符会被脱敏；
                当前试点 UI 不开放保存敏感记忆的选项。
              </p>
              <div className="flex flex-wrap gap-2">
                <Button type="button" onClick={exportMemory} disabled={loading}>
                  导出
                </Button>
                <Button type="button" variant="secondary" onClick={loadProfile} disabled={loading}>
                  刷新
                </Button>
              </div>
            </div>
          </Panel>

          <Panel title="删除记忆">
            <div className="space-y-3">
              <FormHint>
                输入 DELETE 后会删除当前用户拥有的练习记录。账号凭证与练习记忆分开管理。
              </FormHint>
              <input
                value={deleteConfirm}
                onChange={(event) => setDeleteConfirm(event.target.value)}
                className="w-full rounded-md border border-line px-3 py-2 text-sm"
                placeholder="DELETE"
              />
              <Button
                type="button"
                variant="danger"
                onClick={deleteMemory}
                disabled={loading || deleteConfirm !== "DELETE"}
              >
                删除记忆
              </Button>
            </div>
          </Panel>

          <Panel title="删除账号">
            <div className="space-y-3">
              <FormHint>
                输入 DELETE ACCOUNT 后会删除当前登录账号、撤销会话，并清理该账号拥有的练习记录。
                这是账号级操作；如果只想清空练习记录，请使用上面的删除记忆。
              </FormHint>
              <input
                value={accountDeleteConfirm}
                onChange={(event) => setAccountDeleteConfirm(event.target.value)}
                className="w-full rounded-md border border-line px-3 py-2 text-sm"
                placeholder="DELETE ACCOUNT"
              />
              <Button
                type="button"
                variant="danger"
                onClick={deleteAccount}
                disabled={loading || accountDeleteConfirm !== "DELETE ACCOUNT"}
              >
                删除账号
              </Button>
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <ErrorBox
            message={error}
            onRetry={retryAction?.run}
            retrying={loading || approvingConsent}
            retryLabel={retryAction?.label}
          />
          {statusMessage ? (
            <div className="rounded-md border border-line bg-white px-3 py-2 text-sm text-slate-700">
              {statusMessage}
            </div>
          ) : null}

          {pendingConsent ? (
            <DirectConsentCard
              detail={pendingConsent.detail}
              approving={approvingConsent}
              onApprove={approvePendingConsent}
              onReject={rejectPendingConsent}
            />
          ) : null}

          <Panel title="练习偏好">
            <form onSubmit={savePreferences} className="space-y-3">
              <label className="block text-sm font-medium text-slate-700">
                偏好的角色扮演难度
                <Select
                  value={preferences.preferred_roleplay_difficulty ?? ""}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      preferred_roleplay_difficulty: event.target.value
                        ? Number(event.target.value)
                        : null
                    }))
                  }
                  className="mt-1"
                >
                  <option value="">不设置偏好</option>
                  {[1, 2, 3, 4, 5].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                反馈风格
                <Select
                  value={preferences.preferred_feedback_style ?? ""}
                  onChange={(event) =>
                    setPreferences((current) => ({
                      ...current,
                      preferred_feedback_style:
                        (event.target.value as PracticePreferences["preferred_feedback_style"]) ||
                        null
                    }))
                  }
                  className="mt-1"
                >
                  <option value="">不设置偏好</option>
                  {FEEDBACK_STYLE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                偏好的练习场景
                <div className="mt-1 flex gap-2">
                  <input
                    value={newScenarioPreference}
                    maxLength={240}
                    onChange={(event) =>
                      setNewScenarioPreference(event.target.value)
                    }
                    placeholder="输入一个具体练习情境"
                    className="min-w-0 flex-1 rounded-md border border-line px-3 py-2"
                  />
                  <Button
                    type="button"
                    onClick={() => {
                      const value = newScenarioPreference.trim();
                      if (!value) {
                        return;
                      }
                      setPreferences((current) => ({
                        ...current,
                        preferred_practice_scenarios: Array.from(
                          new Set([
                            ...current.preferred_practice_scenarios,
                            value
                          ])
                        ).slice(0, 5)
                      }));
                      setNewScenarioPreference("");
                    }}
                  >
                    添加
                  </Button>
                </div>
              </label>
              <div className="flex flex-wrap gap-2">
                {preferences.preferred_practice_scenarios.length === 0 ? (
                  <Badge>未设置场景偏好</Badge>
                ) : (
                  preferences.preferred_practice_scenarios.map((scenario) => (
                    <button
                      key={scenario}
                      type="button"
                      onClick={() =>
                        setPreferences((current) => ({
                          ...current,
                          preferred_practice_scenarios:
                            current.preferred_practice_scenarios.filter(
                              (item) => item !== scenario
                            )
                        }))
                      }
                      className="rounded-md border border-line px-2 py-1 text-xs text-slate-700 hover:border-brand"
                    >
                      {scenario}
                    </button>
                  ))
                )}
              </div>
              <Button type="submit" disabled={loading}>
                保存偏好
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={disablePreferences}
                disabled={loading}
              >
                关闭长期偏好
              </Button>
            </form>
          </Panel>

          <Panel title="开始前设置">
            <div className="space-y-3 text-sm leading-6 text-slate-700">
              <p>
                登录用户的开始前设置会保存到账号；本地 demo 或缓存状态只保存在当前浏览器。
              </p>
              <div className="flex flex-wrap gap-2">
                <Link
                  href="/onboarding"
                  className="rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
                >
                  重新设置
                </Link>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={resetOnboarding}
                  disabled={loading}
                >
                  重置账号开始前设置
                </Button>
              </div>
            </div>
          </Panel>

          <Panel title="导出预览">
            {exported ? (
              <div className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {savedRecordCounts.map((item) => (
                    <div
                      key={item.name}
                      className="rounded-md border border-line bg-white px-3 py-2"
                    >
                      <div className="text-xs uppercase text-slate-500">{item.name}</div>
                      <div className="mt-1 text-lg font-semibold text-slate-900">
                        {item.count}
                      </div>
                    </div>
                  ))}
                </div>
                <pre className="max-h-[360px] overflow-auto rounded-md border border-line bg-slate-950 p-3 text-xs leading-5 text-slate-100">
                  {JSON.stringify(exported, null, 2)}
                </pre>
              </div>
            ) : (
              <EmptyState
                title="尚未导出"
                description="点击导出后，可以查看当前用户已保存的记录。"
              />
            )}
          </Panel>
        </div>
      </div>
    </AuthGuard>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <dt className="text-xs uppercase text-slate-500">{label}</dt>
      <dd className="text-lg font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

function normalizedPreferences(preferences: PracticePreferences): PracticePreferences {
  return {
    preferred_roleplay_difficulty: preferences.preferred_roleplay_difficulty,
    preferred_feedback_style: preferences.preferred_feedback_style,
    preferred_practice_scenarios: preferences.preferred_practice_scenarios.slice(0, 5)
  };
}

function sumCounts(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

type SavePreferencesRequest = {
  userId: string;
  preferences: PracticePreferences;
};

type PendingSettingsConsent = {
  detail: ConsentRequiredDetail;
  request: SavePreferencesRequest;
};

type RetryAction = {
  label: string;
  run: () => void;
};
