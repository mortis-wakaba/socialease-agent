"use client";

import { FormEvent, useEffect, useState } from "react";
import { ConsentRequiredError, api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { showDiagnostics } from "@/lib/diagnostics";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import { DirectConsentCard } from "@/components/direct-consent-card";
import { PausePracticePanel } from "@/components/pause-practice-panel";
import { SessionReviewPanel } from "@/components/session-review-panel";
import type {
  ConsentRequiredDetail,
  LLMUsage,
  RoleplayFeedback,
  RoleplayRubricBreakdown,
  RoleplaySession
} from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  EmptyState,
  ErrorBox,
  FormHint,
  LLMUsageBadge,
  PageHeader,
  Panel,
  TextArea,
} from "@/components/ui";

const SHOW_DIAGNOSTICS = showDiagnostics();

export default function PracticePage() {
  const auth = useRequireAuth();
  const [scenarioDescription, setScenarioDescription] = useState("");
  const [practiceGoal, setPracticeGoal] = useState("");
  const [difficulty, setDifficulty] = useState(2);
  const [session, setSession] = useState<RoleplaySession | null>(null);
  const [message, setMessage] = useState("我想先说我的核心观点。");
  const [feedback, setFeedback] = useState<RoleplayFeedback | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [citationsExpanded, setCitationsExpanded] = useState(false);
  const [lastTurnUsage, setLastTurnUsage] = useState<LLMUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [pendingConsent, setPendingConsent] = useState<{
    detail: ConsentRequiredDetail;
    request: {
      userId: string;
      scenarioDescription: string;
      practiceGoal: string;
      difficulty: number;
    };
  } | null>(null);
  const [approvingConsent, setApprovingConsent] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    const sessionId = new URLSearchParams(window.location.search).get("session_id");
    if (!sessionId) {
      return;
    }
    void loadExistingSession(sessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, auth.userId]);

  async function loadExistingSession(sessionId: string) {
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.getRoleplaySession(sessionId, currentUserId());
      setSession(result.session);
      setScenarioDescription(
        result.session.scenario_spec?.safe_summary ?? result.session.scenario ?? ""
      );
      setPracticeGoal(result.session.scenario_spec?.practice_goal ?? "");
      setDifficulty(result.session.difficulty);
      setStatus(restoredSessionStatus(result.session));
      setCitationsExpanded(false);
      setLastTurnUsage(null);
      if (
        result.session.status === "completed" &&
        hasUserPracticeMessage(result.session)
      ) {
        await loadFeedbackForSession(result.session);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法恢复会话");
      setRetryAction({
        label: "重试恢复",
        run: () => {
          void loadExistingSession(sessionId);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function startSession() {
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setFeedback(null);
    setPendingConsent(null);
    const request = {
      userId: currentUserId(),
      scenarioDescription,
      practiceGoal,
      difficulty
    };
    try {
      await startSessionWithRequest(request);
    } catch (err) {
      if (err instanceof ConsentRequiredError) {
        setPendingConsent({ detail: err.detail, request });
        setStatus("需要同意");
      } else {
        setError(err instanceof Error ? err.message : "无法开始会话");
        setRetryAction({
          label: "重试开始",
          run: () => {
            void startSessionWithRequest(request);
          }
        });
      }
    } finally {
      setLoading(false);
    }
  }

  async function startSessionWithRequest(
    request: {
      userId: string;
      scenarioDescription: string;
      practiceGoal: string;
      difficulty: number;
    },
    protocolId?: string
  ) {
    const result = await api.startRoleplay(
      request.userId,
      request.scenarioDescription,
      request.difficulty,
      { protocolId, practiceGoal: request.practiceGoal || undefined }
    );
    setSession(result.session);
    setStatus(
      SHOW_DIAGNOSTICS
        ? result.session.retrieved_guidance.no_guidance_found
          ? "备用引导"
          : "知识库引导"
        : "练习已准备好"
    );
    setCitationsExpanded(false);
    setLastTurnUsage(null);
    setPendingConsent(null);
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
      await startSessionWithRequest(
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
      setStatus("已取消同意");
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

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !message.trim()) {
      setError(session ? "发送前请先输入练习回复。" : "请先开始一个练习会话。");
      return;
    }
    if (session.status !== "active") {
      setError("这个角色扮演会话已经暂停或完成，不能继续发送普通练习回复。");
      return;
    }
    const text = message.trim();
    await sendPracticeMessage(session.session_id, text);
  }

  async function sendPracticeMessage(sessionId: string, text: string) {
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.sendRoleplayMessage(
        sessionId,
        currentUserId(),
        text
      );
      setSession(result.session);
      setStatus(result.blocked ? "已暂停普通练习" : "已完成一轮练习");
      setLastTurnUsage(result.llm_usage);
      setMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法发送回复");
      setRetryAction({
        label: "重试发送",
        run: () => {
          void sendPracticeMessage(sessionId, text);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  function resetSession() {
    setSession(null);
    setFeedback(null);
    setStatus(null);
    setError(null);
    setRetryAction(null);
    setLastTurnUsage(null);
    setCitationsExpanded(false);
  }

  async function loadFeedback() {
    if (!session) {
      return;
    }
    await loadFeedbackForSession(session);
  }

  async function loadFeedbackForSession(targetSession: RoleplaySession) {
    if (targetSession.status === "paused") {
      setError("这个角色扮演会话已暂停，不能生成普通练习反馈。");
      return;
    }
    if (!hasUserPracticeMessage(targetSession)) {
      setError("请至少完成一轮练习回复后再获取反馈。");
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.getRoleplayFeedback(targetSession.session_id, currentUserId());
      setSession(result.session);
      setFeedback(result.feedback);
      setStatus("已完成角色扮演");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法生成反馈");
      setRetryAction({
        label: "重试反馈",
        run: () => {
          void loadFeedbackForSession(targetSession);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function pauseRoleplaySession() {
    if (!session) {
      return;
    }
    if (session.status === "completed") {
      setStatus("已完成角色扮演，不能再暂停。");
      return;
    }
    const result = await api.pauseRoleplaySession(session.session_id, currentUserId());
    setSession(result.session);
    setStatus("已暂停角色扮演");
  }

  async function resumeRoleplaySession() {
    if (!session) {
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.resumeRoleplaySession(
        session.session_id,
        currentUserId()
      );
      setSession(result.session);
      setFeedback(null);
      setStatus("已恢复练习");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法继续练习");
      setRetryAction({
        label: "重试继续",
        run: () => {
          void resumeRoleplaySession();
        }
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="角色扮演练习"
        description="描述你真实面对的社交情境，开始低压力模拟练习。你可以随时暂停，也可以从历史记录继续。"
      />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Panel title="练习场景">
            <label className="text-sm font-medium text-ink" htmlFor="scenario-description">
              你想练习什么具体情境？
            </label>
            <TextArea
              id="scenario-description"
              className="mt-2"
              value={scenarioDescription}
              maxLength={1200}
              onChange={(event) => setScenarioDescription(event.target.value)}
              placeholder="例如：小组成员临时把额外任务交给我，我想清楚拒绝，同时保持合作关系。"
            />
            <label className="mt-4 block text-sm font-medium text-ink" htmlFor="practice-goal">
              本次练习目标（可选）
            </label>
            <TextArea
              id="practice-goal"
              className="mt-2"
              value={practiceGoal}
              maxLength={400}
              onChange={(event) => setPracticeGoal(event.target.value)}
              placeholder="例如：先表达理解，再说明边界并提出有限的替代方案。"
            />
            <div className="mt-4 flex items-center gap-3">
              <label className="text-sm text-slate-600">难度</label>
              <input
                type="range"
                min={1}
                max={5}
                value={difficulty}
                onChange={(event) => setDifficulty(Number(event.target.value))}
                className="w-full"
              />
              <Badge>{difficulty}/5</Badge>
            </div>
            <div className="mt-4">
              <Button
                onClick={startSession}
                disabled={loading || !scenarioDescription.trim()}
              >
                开始练习
              </Button>
            </div>
            <div className="mt-2">
              <FormHint>
                这里只做社交表达练习，不做诊断，也不提供治疗承诺。
              </FormHint>
            </div>
          </Panel>
          <ErrorBox
            message={error}
            onRetry={retryAction?.run}
            retrying={loading || approvingConsent}
            retryLabel={retryAction?.label}
          />
          {pendingConsent ? (
            <DirectConsentCard
              detail={pendingConsent.detail}
              approving={approvingConsent}
              onApprove={approvePendingConsent}
              onReject={rejectPendingConsent}
            />
          ) : null}
          <PausePracticePanel
            initialPaused={session?.status === "paused"}
            onPersistPause={
              session && session.status !== "completed"
                ? pauseRoleplaySession
                : undefined
            }
            persistedMessage="已保存角色扮演暂停状态。"
          />
        </div>

        <div className="space-y-4">
          <Panel
            title="对话练习"
            action={status ? <Badge tone={status.startsWith("已暂停") ? "danger" : "info"}>{status}</Badge> : null}
          >
            {session ? (
              <div className="space-y-3">
                <div className="rounded-md border border-line bg-panel p-3">
                  <div className="mb-2 flex flex-wrap gap-2">
                    <Badge tone="info">{scenarioTitle(session)}</Badge>
                    {SHOW_DIAGNOSTICS ? (
                      <Badge>{session.session_id.slice(0, 8)}</Badge>
                    ) : null}
                    <Badge tone={session.retrieved_guidance.no_guidance_found ? "warn" : "good"}>
                      {SHOW_DIAGNOSTICS
                        ? session.retrieved_guidance.no_guidance_found
                          ? "未找到引导"
                          : "知识库引导"
                        : session.retrieved_guidance.no_guidance_found
                          ? "已使用备用说明"
                          : "基于练习资料"}
                    </Badge>
                    <Badge tone="neutral">
                      {session.messages.length} 条消息
                    </Badge>
                    <Badge tone={session.status === "paused" ? "warn" : session.status === "completed" ? "good" : "info"}>
                      {roleplayStatusLabel(session.status)}
                    </Badge>
                    {SHOW_DIAGNOSTICS && lastTurnUsage ? (
                      <LLMUsageBadge usage={lastTurnUsage} />
                    ) : null}
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => setCitationsExpanded((value) => !value)}
                  >
                    {citationsExpanded ? "收起来源" : "查看来源"}
                  </Button>
                  {citationsExpanded && (
                    <div className="mt-3">
                      <CitationList citations={session.retrieved_guidance.citations} />
                    </div>
                  )}
                </div>
                <div className="max-h-[360px] space-y-3 overflow-y-auto rounded-md border border-line bg-panel p-3">
                  {session.messages.map((item, index) => (
                    <div key={`${item.created_at}-${index}`} className="rounded-md border border-line bg-white p-3">
                      <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                        {item.role}
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                        {item.content}
                      </p>
                    </div>
                  ))}
                </div>
                {session.status === "paused" ? (
                  <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-amber-900">
                    <p>
                      这个会话已暂停。点击继续练习后，可以发送下一轮练习回复；完成至少一轮后再获取结构化反馈。
                    </p>
                    <div className="mt-3">
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={resumeRoleplaySession}
                        disabled={loading}
                      >
                        继续练习
                      </Button>
                    </div>
                  </div>
                ) : null}
                <form onSubmit={sendMessage} className="space-y-3">
                  <TextArea
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    placeholder="输入你的练习回复..."
                  />
                  <div className="flex gap-2">
                    <Button
                      type="submit"
                      disabled={loading || session.status !== "active"}
                    >
                      发送一轮
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={loadFeedback}
                      disabled={
                        loading ||
                        session.status === "paused" ||
                        !hasUserPracticeMessage(session)
                      }
                    >
                      {session.status === "completed" ? "查看反馈" : "获取反馈"}
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={resetSession}
                      disabled={loading}
                    >
                      清空当前视图
                    </Button>
                  </div>
                  <FormHint>{roleplayActionHint(session)}</FormHint>
                </form>
              </div>
            ) : (
              <EmptyState
                title="暂无进行中的练习"
                description="描述具体情境并选择难度后开始练习。练习会保存为会话，可从历史记录继续。"
              />
            )}
          </Panel>

          {feedback && (
            <>
              <Panel title="练习反馈">
                <div className="mb-4 grid gap-2 sm:grid-cols-4">
                  <Badge tone="info">清晰 {feedback.clarity_score}/5</Badge>
                  <Badge tone="info">自然 {feedback.naturalness_score}/5</Badge>
                  <Badge tone="info">坚定 {feedback.assertiveness_score}/5</Badge>
                  <Badge tone="info">共情 {feedback.empathy_score}/5</Badge>
                </div>
                {feedback.rubric_breakdown.length > 0 ? (
                  <div className="mb-4 grid gap-3 lg:grid-cols-2">
                    {feedback.rubric_breakdown.map((item) => (
                      <RubricCard key={item.dimension} breakdown={item} />
                    ))}
                  </div>
                ) : null}
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <h3 className="mb-2 text-sm font-medium text-ink">做得好的地方</h3>
                    <ul className="space-y-1 text-sm text-slate-700">
                      {feedback.strengths.map((item) => (
                        <li key={item}>- {item}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h3 className="mb-2 text-sm font-medium text-ink">下一步建议</h3>
                    <ul className="space-y-1 text-sm text-slate-700">
                      {feedback.suggestions.map((item) => (
                        <li key={item}>- {item}</li>
                      ))}
                    </ul>
                  </div>
                </div>
                <div className="mt-4 rounded-md border border-line bg-panel p-3 text-sm text-slate-700">
                  {feedback.next_try_prompt}
                </div>
                <div className="mt-4">
                  <CitationList citations={feedback.citations} />
                </div>
              </Panel>
              <SessionReviewPanel
                title="角色扮演复盘"
                source="roleplay"
                sourceId={session?.session_id ?? null}
              />
            </>
          )}
        </div>
      </div>
    </AuthGuard>
  );
}

function RubricCard({ breakdown }: { breakdown: RoleplayRubricBreakdown }) {
  const presentSignals = breakdown.signals.filter((signal) => signal.present);
  const missingSignals = breakdown.signals.filter((signal) => !signal.present);

  return (
    <div className="rounded-md border border-line bg-panel p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">
            {dimensionLabel(breakdown.dimension)}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            {breakdown.rationale}
          </p>
        </div>
        <Badge tone={scoreTone(breakdown.score)}>{breakdown.score}/5</Badge>
      </div>
      <SignalGroup
        title="已体现的信号"
        empty="还没有检测到明显信号。"
        signals={presentSignals}
        tone="good"
      />
      <div className="mt-3">
        <SignalGroup
          title="下一步关注"
          empty="这个维度已经覆盖得不错。"
          signals={missingSignals}
          tone="neutral"
        />
      </div>
    </div>
  );
}

function SignalGroup({
  title,
  empty,
  signals,
  tone
}: {
  title: string;
  empty: string;
  signals: RoleplayRubricBreakdown["signals"];
  tone: "good" | "neutral";
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase text-slate-500">
        {title}
      </div>
      {signals.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {signals.map((signal) => (
            <span
              key={signal.name}
              className={`rounded-md border px-2 py-1 text-xs ${
                tone === "good"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-slate-200 bg-white text-slate-600"
              }`}
              title={`weight ${signal.weight}`}
            >
              {signal.label}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-xs leading-5 text-slate-500">{empty}</p>
      )}
    </div>
  );
}

function dimensionLabel(dimension: string) {
  const labels: Record<string, string> = {
    clarity: "清晰度",
    naturalness: "自然度",
    assertiveness: "坚定表达",
    empathy: "共情"
  };
  return labels[dimension] ?? dimension;
}

function scoreTone(score: number) {
  if (score >= 4) {
    return "good" as const;
  }
  if (score === 3) {
    return "warn" as const;
  }
  return "neutral" as const;
}

function scenarioTitle(session: RoleplaySession) {
  return session.scenario_spec?.safe_summary ?? session.scenario ?? "练习场景";
}

function restoredSessionStatus(session: RoleplaySession) {
  if (session.status === "paused") {
    return "已暂停角色扮演";
  }
  if (session.status === "completed") {
    return "已完成角色扮演";
  }
  if (SHOW_DIAGNOSTICS) {
    return session.retrieved_guidance.no_guidance_found
      ? "已恢复：使用备用引导"
      : "已恢复：知识库引导";
  }
  return "已恢复练习";
}

function roleplayStatusLabel(status: RoleplaySession["status"]) {
  const labels: Record<RoleplaySession["status"], string> = {
    active: "进行中",
    paused: "已暂停",
    completed: "已完成"
  };
  return labels[status];
}

function hasUserPracticeMessage(session: RoleplaySession) {
  return session.messages.some((item) => item.role === "user");
}

function roleplayActionHint(session: RoleplaySession) {
  if (session.status === "paused") {
    return "这个会话已暂停。点击继续练习后，可以发送下一轮；完成至少一轮后再获取反馈。";
  }
  if (session.status === "completed") {
    return "这个会话已完成，可查看反馈或开始新的练习。";
  }
  if (!hasUserPracticeMessage(session)) {
    return "完成至少一轮练习回复后，可以生成结构化反馈。";
  }
  return "可以继续练习，也可以获取当前会话的结构化反馈。";
}

type RetryAction = {
  label: string;
  run: () => void;
};
