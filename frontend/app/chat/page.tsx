"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { showDiagnostics } from "@/lib/diagnostics";
import { getOnboardingState } from "@/lib/onboarding";
import { useRequireAuth } from "@/lib/use-require-auth";
import type {
  ChatProgressEvent,
  ChatResponse,
  ChatWorkflowStage
} from "@/lib/types";
import { AuthGuard } from "@/components/auth-guard";
import { HarnessActionCard } from "@/components/harness-action-card";
import { PausePracticePanel } from "@/components/pause-practice-panel";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  LLMUsageBadge,
  PageHeader,
  Panel,
  TextArea,
  riskTone
} from "@/components/ui";

type ChatMessage = {
  role: "user" | "agent";
  content: string;
  result?: ChatResponse;
  sourceRequest?: {
    message: string;
    context: Record<string, unknown>;
  };
};

type RunProgress = {
  runId: string | null;
  completedStages: ChatWorkflowStage[];
  stageLatencies: Partial<Record<ChatWorkflowStage, number>>;
  elapsedMs: number;
};

const EMPTY_PROGRESS: RunProgress = {
  runId: null,
  completedStages: [],
  stageLatencies: {},
  elapsedMs: 0
};

const WORKFLOW_STAGES: ChatWorkflowStage[] = [
  "safety",
  "routing",
  "skill",
  "output_guardrail",
  "trace"
];

const SHOW_DIAGNOSTICS = showDiagnostics();

export default function ChatPage() {
  const auth = useRequireAuth();
  const [input, setInput] = useState("我想模拟课堂发言，怕自己说不清楚");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [latest, setLatest] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [approvingProtocolId, setApprovingProtocolId] = useState<string | null>(null);
  const [onboardingDone, setOnboardingDone] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [activeProgress, setActiveProgress] = useState<RunProgress>(EMPTY_PROGRESS);
  const [latestProgress, setLatestProgress] = useState<RunProgress | null>(null);
  const [waitingMs, setWaitingMs] = useState(0);
  const progressRef = useRef<RunProgress>(EMPTY_PROGRESS);
  const requestStartedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    async function refreshOnboarding() {
      try {
        const result = await api.getOnboardingProfile(auth.userId as string);
        setOnboardingDone(result.onboarding_profile.boundary_acknowledged);
      } catch {
        setOnboardingDone(Boolean(getOnboardingState()?.completed));
      }
    }
    function handleOnboardingChanged() {
      void refreshOnboarding();
    }
    void refreshOnboarding();
    window.addEventListener("socialease:onboarding", handleOnboardingChanged);
    return () =>
      window.removeEventListener("socialease:onboarding", handleOnboardingChanged);
  }, [auth.ready, auth.authenticated, auth.userId]);

  useEffect(() => {
    if (!loading) {
      return;
    }
    const timer = window.setInterval(() => {
      if (requestStartedAtRef.current !== null) {
        setWaitingMs(Date.now() - requestStartedAtRef.current);
      }
    }, 200);
    return () => window.clearInterval(timer);
  }, [loading]);

  function beginProgress() {
    const progress = { ...EMPTY_PROGRESS, stageLatencies: {} };
    progressRef.current = progress;
    setActiveProgress(progress);
    setWaitingMs(0);
    requestStartedAtRef.current = Date.now();
  }

  function handleProgress(event: ChatProgressEvent) {
    const previous = progressRef.current;
    const completedStages =
      event.type === "stage_completed" && event.stage
        ? Array.from(new Set([...previous.completedStages, event.stage]))
        : previous.completedStages;
    const stageLatencies = { ...previous.stageLatencies };
    if (
      event.type === "stage_completed" &&
      event.stage &&
      event.stage_latency_ms !== null
    ) {
      stageLatencies[event.stage] = event.stage_latency_ms;
    }
    const next: RunProgress = {
      runId: event.run_id,
      completedStages,
      stageLatencies,
      elapsedMs: event.elapsed_ms
    };
    progressRef.current = next;
    setActiveProgress(next);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loading || approvingProtocolId) {
      return;
    }
    const trimmed = input.trim();
    if (!trimmed) {
      setError("发送前请先输入一段内容。");
      return;
    }
    const sourceRequest = { message: trimmed, context: {} };
    setMessages((items) => [...items, { role: "user", content: trimmed }]);
    setInput("");
    await sendChatRequest(sourceRequest);
  }

  async function sendChatRequest(sourceRequest: {
    message: string;
    context: Record<string, unknown>;
  }) {
    setLoading(true);
    beginProgress();
    setError(null);
    setRetryAction(null);
    try {
      const result = await api.chatStream(
        currentUserId(),
        sourceRequest.message,
        sourceRequest.context,
        { onProgress: handleProgress }
      );
      setLatest(result);
      setLatestProgress(progressRef.current);
      setMessages((items) => [
        ...items,
        { role: "agent", content: result.response, result, sourceRequest }
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
      setRetryAction({
        label: "重试发送",
        run: () => {
          void sendChatRequest(sourceRequest);
        }
      });
    } finally {
      setLoading(false);
      setActiveProgress(EMPTY_PROGRESS);
      requestStartedAtRef.current = null;
    }
  }

  async function handleApproveConsent(
    protocolId: string,
    sourceRequest: { message: string; context: Record<string, unknown> }
  ) {
    setApprovingProtocolId(protocolId);
    setError(null);
    setRetryAction(null);
    try {
      const userId = currentUserId();
      await api.respondToProtocol(protocolId, userId, true);
      setMessages((items) => [
        ...items,
        { role: "user", content: "同意继续这个练习。" }
      ]);
      setLoading(true);
      beginProgress();
      const result = await api.chatStream(
        userId,
        sourceRequest.message,
        {
          ...sourceRequest.context,
          protocol_id: protocolId
        },
        { onProgress: handleProgress }
      );
      setLatest(result);
      setLatestProgress(progressRef.current);
      setMessages((items) => [
        ...items,
        { role: "agent", content: result.response, result, sourceRequest }
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法批准同意");
      setRetryAction({
        label: "重试同意",
        run: () => {
          void handleApproveConsent(protocolId, sourceRequest);
        }
      });
    } finally {
      setLoading(false);
      setActiveProgress(EMPTY_PROGRESS);
      requestStartedAtRef.current = null;
      setApprovingProtocolId(null);
    }
  }

  async function handleRejectConsent(protocolId: string) {
    setApprovingProtocolId(protocolId);
    setError(null);
    setRetryAction(null);
    try {
      await api.respondToProtocol(protocolId, currentUserId(), false);
      setMessages((items) => [
        ...items,
        {
          role: "agent",
          content: "已取消本次练习。你仍然可以继续做支持性整理或选择更低强度的练习。"
        }
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法取消同意");
      setRetryAction({
        label: "重试取消",
        run: () => {
          void handleRejectConsent(protocolId);
        }
      });
    } finally {
      setApprovingProtocolId(null);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="安全对话"
        description="描述一个社交压力场景。系统会先做安全判断，再决定进入支持、练习、反思或资源导航。"
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Panel title="对话">
          <div className="mb-4 min-h-[360px] space-y-3 rounded-md border border-line bg-panel p-3">
            {messages.length === 0 ? (
              onboardingDone ? (
                <EmptyState
                  title="还没有对话"
                  description="发送一段社交压力描述后，这里会显示系统回复和下一步入口。"
                />
              ) : (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                  <div className="font-medium">建议先完成开始前设置</div>
                  <p className="mt-1">
                    用一分钟选择练习目标、偏好场景和当前强度。你也可以跳过，直接发送消息。
                  </p>
                  <Link
                    href="/onboarding"
                    className="mt-3 inline-flex rounded-md border border-amber-300 bg-white px-3 py-2 font-medium text-amber-900 hover:border-brand"
                  >
                    去设置
                  </Link>
                </div>
              )
            ) : (
              messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`rounded-lg border p-3 ${
                    message.role === "user"
                      ? "ml-auto max-w-[85%] border-brand bg-white"
                      : "mr-auto max-w-[90%] border-line bg-white"
                  }`}
                >
                  <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                    {message.role === "user" ? "你" : "SocialEase"}
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                    {message.content}
                  </p>
                  {message.result ? (
                    <HarnessActionCard
                      result={message.result}
                      approving={
                        approvingProtocolId ===
                        readString(message.result.structured_data.protocol_id)
                      }
                      onApproveConsent={
                        message.sourceRequest
                          ? (protocolId) =>
                              handleApproveConsent(protocolId, message.sourceRequest!)
                          : undefined
                      }
                      onRejectConsent={handleRejectConsent}
                    />
                  ) : null}
                </div>
              ))
            )}
            {loading ? (
              <RunStatusCard progress={activeProgress} waitingMs={waitingMs} />
            ) : null}
          </div>
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="输入一个社交压力场景..."
              disabled={loading || Boolean(approvingProtocolId)}
            />
            <div className="flex items-center justify-between gap-3">
              <ErrorBox
                message={error}
                onRetry={retryAction?.run}
                retrying={loading || Boolean(approvingProtocolId)}
                retryLabel={retryAction?.label}
              />
              <Button
                type="submit"
                disabled={loading || Boolean(approvingProtocolId)}
              >
                {loading ? "发送中..." : "发送"}
              </Button>
            </div>
          </form>
        </Panel>

        <Panel title={SHOW_DIAGNOSTICS ? "开发诊断" : "练习状态"}>
          {latest ? (
            SHOW_DIAGNOSTICS ? (
              <DeveloperDiagnosticsPanel
                latest={latest}
                progress={
                  latestProgress?.runId === latest.run_id ? latestProgress : null
                }
              />
            ) : (
              <ProductStatusPanel latest={latest} />
            )
          ) : (
            <p className="text-sm text-slate-500">还没有运行记录。</p>
          )}
        </Panel>
        <PausePracticePanel interventionPlanId={latest?.trace.intervention_plan_id ?? null} />
      </div>
    </AuthGuard>
  );
}

function ProductStatusPanel({ latest }: { latest: ChatResponse }) {
  const action = readString(latest.structured_data.action);
  const isCrisis = latest.risk_level === "crisis" || action === "crisis_escalation";
  const status = isCrisis
    ? "已暂停普通练习"
    : action === "consent_required"
      ? "等待你确认是否继续"
      : action === "roleplay_started"
        ? "已准备好角色扮演练习"
        : action === "worksheet_created"
          ? "已生成结构化反思"
          : action === "exposure_plan_created"
            ? "已生成社交练习计划"
            : action === "clarification_requested"
              ? "需要你再补充一点信息"
              : action === "calendar_proposal_created"
                ? "日历预览等待确认"
              : action === "out_of_scope"
                ? "这个请求超出当前产品范围"
            : "可以继续对话或选择一个低强度练习";

  return (
    <div className="space-y-3 text-sm leading-6 text-slate-700">
      <div className="rounded-md border border-line bg-panel p-3">
        <div className="font-medium text-ink">{status}</div>
        <p className="mt-1">
          如果你感觉不适，可以先暂停；需要现实支持时，优先联系可信任的人、学校心理中心或当地紧急服务。
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Link
          href="/history"
          className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
        >
          查看历史
        </Link>
        <Link
          href="/support"
          className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
        >
          支持资源
        </Link>
      </div>
    </div>
  );
}

function RunStatusCard({
  progress,
  waitingMs
}: {
  progress: RunProgress;
  waitingMs: number;
}) {
  const nextStage = WORKFLOW_STAGES.find(
    (stage) => !progress.completedStages.includes(stage)
  );
  const status = nextStage ? stageActiveLabel(nextStage) : "正在准备安全回复";

  return (
    <div
      className="mr-auto max-w-[90%] rounded-lg border border-emerald-200 bg-emerald-50 p-3"
      role="status"
      aria-live="polite"
    >
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-emerald-900">
        <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-600" />
        {status}
      </div>
      <div className="flex flex-wrap gap-2">
        {WORKFLOW_STAGES.map((stage) => {
          const completed = progress.completedStages.includes(stage);
          return (
            <span
              key={stage}
              className={`rounded-full px-2 py-1 text-xs ${
                completed
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-white text-slate-500"
              }`}
            >
              {completed ? "✓ " : ""}
              {stageShortLabel(stage)}
            </span>
          );
        })}
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-600">
        已等待 {(waitingMs / 1000).toFixed(1)} 秒。最终回复会在输出安全检查完成后展示。
      </p>
    </div>
  );
}

function DeveloperDiagnosticsPanel({
  latest,
  progress
}: {
  latest: ChatResponse;
  progress: RunProgress | null;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Badge tone={riskTone(latest.risk_level)}>
          风险: {latest.risk_level}
        </Badge>
        <Badge tone="info">意图: {latest.intent}</Badge>
        <LLMUsageBadge usage={latest.trace.safety_result.llm_usage} />
        <LLMUsageBadge usage={latest.trace.intent_result.llm_usage} />
      </div>
      <div>
        <div className="text-xs font-medium uppercase text-slate-500">run_id</div>
        <p className="break-all rounded-md border border-line bg-panel p-2 text-xs text-slate-700">
          {latest.run_id}
        </p>
      </div>
      <div className="rounded-md border border-line p-3 text-sm">
        <div className="font-medium text-ink">安全判断原因</div>
        <div className="mt-1 text-slate-700">{latest.trace.safety_result.reason}</div>
      </div>
      <div className="rounded-md border border-line p-3 text-sm">
        <div className="font-medium text-ink">路由原因</div>
        <div className="mt-1 text-slate-700">{latest.trace.intent_result.reason}</div>
      </div>
      <div className="rounded-md border border-line p-3 text-sm">
        <div className="font-medium text-ink">选择的 Agent</div>
        <div className="mt-1 text-slate-700">{latest.trace.selected_agent}</div>
      </div>
      <div className="rounded-md border border-line p-3 text-sm">
        <div className="font-medium text-ink">延迟</div>
        <div className="mt-1 text-slate-700">
          {latest.trace.latency_ms.toFixed(2)} ms
        </div>
      </div>
      {progress ? (
        <div className="rounded-md border border-line p-3 text-sm">
          <div className="font-medium text-ink">工作流阶段耗时</div>
          <dl className="mt-2 space-y-1 text-slate-700">
            {WORKFLOW_STAGES.map((stage) => (
              <div key={stage} className="flex justify-between gap-3">
                <dt>{stageShortLabel(stage)}</dt>
                <dd>
                  {progress.stageLatencies[stage] !== undefined
                    ? `${progress.stageLatencies[stage]!.toFixed(2)} ms`
                    : "—"}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function stageShortLabel(stage: ChatWorkflowStage): string {
  return {
    safety: "安全检查",
    routing: "意图路由",
    skill: "执行任务",
    output_guardrail: "输出检查",
    trace: "运行记录"
  }[stage];
}

function stageActiveLabel(stage: ChatWorkflowStage): string {
  return {
    safety: "正在进行安全检查",
    routing: "正在理解你的需求",
    skill: "正在执行合适的支持步骤",
    output_guardrail: "正在检查回复安全性",
    trace: "正在完成运行记录"
  }[stage];
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

type RetryAction = {
  label: string;
  run: () => void;
};
