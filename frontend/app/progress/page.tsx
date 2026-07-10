"use client";

import { FormEvent, useEffect, useState } from "react";
import { ConsentRequiredError, api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import { DirectConsentCard } from "@/components/direct-consent-card";
import { PausePracticePanel } from "@/components/pause-practice-panel";
import { SessionReviewPanel } from "@/components/session-review-panel";
import type {
  ConsentRequiredDetail,
  ExposurePlan,
  ExposureTask,
  InterventionPlanView
} from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  EmptyState,
  ErrorBox,
  FormHint,
  PageHeader,
  Panel,
  TextArea,
  TextInput
} from "@/components/ui";

export default function ProgressPage() {
  const auth = useRequireAuth();
  const [targetScenario, setTargetScenario] = useState("课堂发言");
  const [anxietyLevel, setAnxietyLevel] = useState(7);
  const [previousAttempts, setPreviousAttempts] = useState("写过开场白");
  const [plan, setPlan] = useState<ExposurePlan | null>(null);
  const [interventionPlan, setInterventionPlan] =
    useState<InterventionPlanView | null>(null);
  const [selectedTask, setSelectedTask] = useState<ExposureTask | null>(null);
  const [reflection, setReflection] = useState("完成后发现比想象中可控。");
  const [anxietyAfter, setAnxietyAfter] = useState(4);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [pendingConsent, setPendingConsent] = useState<PendingExposureConsent | null>(null);
  const [approvingConsent, setApprovingConsent] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    const queryPlanId = new URLSearchParams(window.location.search).get("plan_id");
    void loadExistingPlan(queryPlanId, auth.userId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, auth.userId]);

  async function loadExistingPlan(queryPlanId: string | null, userId: string) {
    setError(null);
    setRetryAction(null);
    if (queryPlanId) {
      try {
        const interventionResult = await api.getInterventionPlan(queryPlanId, userId);
        setInterventionPlan(interventionResult.plan);
        if (isLinkedExposurePlan(interventionResult.plan)) {
          try {
            const exposureResult = await api.getExposurePlan(
              interventionResult.plan.session_id,
              userId
            );
            applyExposurePlanResult(exposureResult, queryPlanId);
            setInterventionPlan(exposureResult.intervention_plan ?? interventionResult.plan);
            setStatusMessage("已恢复社交练习阶梯和关联计划状态。");
          } catch (err) {
            setStatusMessage("已恢复计划状态，但完整练习阶梯暂时无法载入。");
            setError(err instanceof Error ? err.message : "无法载入练习阶梯");
          }
          return;
        }
        setStatusMessage("已恢复从对话中创建的练习计划状态。");
        return;
      } catch {
        // Fall through: older links may point to an exposure plan id.
      }
    }
    const loadPlan = queryPlanId
      ? api.getExposurePlan(queryPlanId, userId)
      : api.getUserExposure(userId);
    await loadPlan
      .then((result) => {
        applyExposurePlanResult(result, queryPlanId);
        if (queryPlanId) {
          setStatusMessage("已恢复从对话中创建的社交练习计划。");
        }
      })
      .catch((err) => {
        setStatusMessage(
          queryPlanId
            ? "无法为当前用户恢复这个计划。"
            : null
        );
        setError(err instanceof Error ? err.message : "无法载入计划");
        setRetryAction({
          label: "重试载入",
          run: () => {
            void loadExistingPlan(queryPlanId, userId);
          }
        });
      });
  }

  function applyExposurePlanResult(
    result: { plan: ExposurePlan | null; intervention_plan?: InterventionPlanView | null },
    queryPlanId: string | null
  ) {
    setPlan(result.plan);
    setInterventionPlan(result.intervention_plan ?? null);
    if (!result.plan) {
      return;
    }
    const recommendedTask =
      result.plan.tasks.find(
        (task) => task.task_id === result.plan?.recommended_next_task_id
      ) ?? result.plan.tasks[0] ?? null;
    setSelectedTask(recommendedTask);
    if (queryPlanId) {
      setTargetScenario(result.plan.target_scenario);
      setAnxietyLevel(result.plan.current_anxiety_level);
      setPreviousAttempts(result.plan.previous_attempts.join("\n"));
    }
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!targetScenario.trim()) {
      setError("请输入目标社交场景。");
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setStatusMessage(null);
    setPendingConsent(null);
    const request: CreatePlanRequest = {
      kind: "create_plan",
      userId: currentUserId(),
      targetScenario,
      anxietyLevel,
      previousAttempts: previousAttempts
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean)
    };
    try {
      await createPlanWithRequest(request);
    } catch (err) {
      if (err instanceof ConsentRequiredError) {
        setPendingConsent({ detail: err.detail, request });
        setStatusMessage("创建练习阶梯前需要明确同意。");
      } else {
        setError(err instanceof Error ? err.message : "无法创建计划");
        setRetryAction({
          label: "重试创建",
          run: () => {
            void createPlanWithRequest(request);
          }
        });
      }
    } finally {
      setLoading(false);
    }
  }

  async function createPlanWithRequest(request: CreatePlanRequest, protocolId?: string) {
    const result = await api.createExposurePlan(
      request.userId,
      request.targetScenario,
      request.anxietyLevel,
      request.previousAttempts,
      { protocolId }
    );
    if (result.blocked || !result.plan) {
      setPlan(null);
      setStatusMessage(result.response);
      return;
    }
    setPlan(result.plan);
    setInterventionPlan(result.intervention_plan ?? null);
    setSelectedTask(result.plan.tasks[0] ?? null);
    setStatusMessage(result.response);
    setPendingConsent(null);
  }

  async function completeTask(status: "completed" | "skipped" | "too_hard") {
    if (!selectedTask) {
      setError("提交反馈前请先选择一个任务。");
      return;
    }
    if (!reflection.trim()) {
      setError("提交反馈前请写一小段反思。");
      return;
    }
    setLoading(true);
    setError(null);
    setRetryAction(null);
    setPendingConsent(null);
    const request: CompleteTaskRequest = {
      kind: "complete_task",
      userId: currentUserId(),
      taskId: selectedTask.task_id,
      status,
      anxietyBefore: anxietyLevel,
      anxietyAfter,
      reflection
    };
    try {
      await completeTaskWithRequest(request);
    } catch (err) {
      if (err instanceof ConsentRequiredError) {
        setPendingConsent({ detail: err.detail, request });
        setStatusMessage("记录任务反馈前需要明确同意。");
      } else {
        setError(err instanceof Error ? err.message : "无法更新任务");
        setRetryAction({
          label: "重试提交",
          run: () => {
            void completeTaskWithRequest(request);
          }
        });
      }
    } finally {
      setLoading(false);
    }
  }

  async function completeTaskWithRequest(request: CompleteTaskRequest, protocolId?: string) {
    const result = await api.completeExposureTask(
      request.userId,
      request.taskId,
      request.status,
      request.anxietyBefore,
      request.anxietyAfter,
      request.reflection,
      { protocolId }
    );
    setPlan(result.plan);
    setSelectedTask(result.next_task);
    setStatusMessage(result.adjustment_reason);
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
      if (pendingConsent.request.kind === "create_plan") {
        await createPlanWithRequest(
          pendingConsent.request,
          pendingConsent.detail.protocol_id
        );
      } else {
        await completeTaskWithRequest(
          pendingConsent.request,
          pendingConsent.detail.protocol_id
        );
      }
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
      setStatusMessage("已取消同意，没有保存本次练习更新。");
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
        title="社交练习计划"
        description="创建由易到难的练习阶梯，并根据完成反馈调整下一步。这里是社交练习计划，不是医疗服务。"
      />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Panel title="创建计划">
            <form onSubmit={createPlan} className="space-y-3">
              <label className="block text-sm text-slate-600">
                目标场景
                <TextInput
                  value={targetScenario}
                  onChange={(event) => setTargetScenario(event.target.value)}
                  className="mt-1"
                />
              </label>
              <label className="block text-sm text-slate-600">
                当前焦虑等级
                <div className="mt-2 flex items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={10}
                    value={anxietyLevel}
                    onChange={(event) => setAnxietyLevel(Number(event.target.value))}
                    className="w-full"
                  />
                  <Badge>{anxietyLevel}/10</Badge>
                </div>
              </label>
              <label className="block text-sm text-slate-600">
                之前尝试
                <TextArea
                  value={previousAttempts}
                  onChange={(event) => setPreviousAttempts(event.target.value)}
                  className="mt-1 min-h-24"
                />
              </label>
              <Button type="submit" disabled={loading}>
                创建阶梯
              </Button>
              <FormHint>
                这是社交练习阶梯，可以暂停，也可以根据反馈调整。
              </FormHint>
            </form>
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
          {statusMessage && (
            <Panel title="状态更新">
              <p className="text-sm leading-6 text-slate-700">{statusMessage}</p>
            </Panel>
          )}
          <PausePracticePanel
            interventionPlanId={interventionPlan?.plan_id ?? null}
            initialPaused={interventionPlan?.status === "paused"}
            onPaused={setInterventionPlan}
          />
        </div>

        <div className="space-y-4">
          {interventionPlan ? (
            <Panel
              title="对话练习计划状态"
              action={<Badge tone={interventionPlan.status === "paused" ? "warn" : "info"}>
                {statusLabel(interventionPlan.status)}
              </Badge>}
            >
              <div className="space-y-3 text-sm leading-6 text-slate-700">
                {interventionPlan.timeline.map((step) => (
                  <div key={step.step_id} className="rounded-md border border-line bg-white p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium text-ink">{step.title}</div>
                      <Badge tone={step.status === "completed" ? "good" : step.status === "cancelled" ? "warn" : "neutral"}>
                        {step.status}
                      </Badge>
                    </div>
                    {step.result_summary ? (
                      <p className="mt-1 text-xs text-slate-500">{step.result_summary}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            </Panel>
          ) : null}
          {plan ? (
            <>
              <Panel
                title="练习阶梯"
                action={<Badge tone="info">{plan.plan_id.slice(0, 8)}</Badge>}
              >
                <div className="mb-3 rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
                  {plan.disclaimer}
                </div>
                <div className="space-y-3">
                  {plan.tasks.map((task) => {
                    const isRecommended =
                      task.task_id === plan.recommended_next_task_id;
                    const isSelected = task.task_id === selectedTask?.task_id;
                    return (
                      <button
                        key={task.task_id}
                        onClick={() => setSelectedTask(task)}
                        className={`w-full rounded-lg border p-3 text-left hover:border-brand ${
                          isSelected ? "border-brand bg-emerald-50" : "border-line bg-white"
                        }`}
                      >
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <span className="font-medium text-ink">{task.title}</span>
                          <Badge>难度 {task.difficulty}/10</Badge>
                          {isRecommended && <Badge tone="good">推荐下一步</Badge>}
                        </div>
                        <p className="text-sm leading-6 text-slate-700">
                          {task.description}
                        </p>
                        <p className="mt-2 text-xs text-slate-500">
                          {task.estimated_time_minutes} 分钟 · {task.success_criteria}
                        </p>
                      </button>
                    );
                  })}
                </div>
              </Panel>

              {selectedTask && (
                <>
                  <Panel title="任务反馈">
                    <div className="space-y-3">
                      <div className="rounded-md border border-line bg-panel p-3">
                        <div className="font-medium text-ink">{selectedTask.title}</div>
                        <p className="mt-1 text-sm leading-6 text-slate-700">
                          {selectedTask.fallback_task}
                        </p>
                      </div>
                      <label className="block text-sm text-slate-600">
                        完成后的焦虑等级
                        <div className="mt-2 flex items-center gap-3">
                          <input
                            type="range"
                            min={1}
                            max={10}
                            value={anxietyAfter}
                            onChange={(event) => setAnxietyAfter(Number(event.target.value))}
                            className="w-full"
                          />
                          <Badge>{anxietyAfter}/10</Badge>
                        </div>
                      </label>
                      <TextArea
                        value={reflection}
                        onChange={(event) => setReflection(event.target.value)}
                        className="min-h-24"
                      />
                      <div className="flex flex-wrap gap-2">
                        <Button onClick={() => completeTask("completed")} disabled={loading}>
                          已完成
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => completeTask("skipped")}
                          disabled={loading}
                        >
                          跳过
                        </Button>
                        <Button
                          variant="danger"
                          onClick={() => completeTask("too_hard")}
                          disabled={loading}
                        >
                          太难了
                        </Button>
                      </div>
                      <CitationList citations={selectedTask.citations} />
                    </div>
                  </Panel>
                  <SessionReviewPanel
                    title="社交练习复盘"
                    source="exposure"
                    sourceId={plan.plan_id}
                    defaultBefore={anxietyLevel}
                    defaultAfter={anxietyAfter}
                  />
                </>
              )}
              <Panel title="尝试记录">
                {plan.attempts.length > 0 ? (
                  <div className="space-y-3">
                    {plan.attempts.map((attempt, index) => (
                      <div key={`${attempt.task_id}-${attempt.created_at}`} className="rounded-md border border-line p-3 text-sm">
                        <div className="mb-2 flex flex-wrap gap-2">
                          <Badge>#{index + 1}</Badge>
                          <Badge tone={attempt.status === "completed" ? "good" : attempt.status === "too_hard" ? "danger" : "warn"}>
                            {attempt.status}
                          </Badge>
                          <Badge>
                            焦虑 {attempt.anxiety_before} → {attempt.anxiety_after}
                          </Badge>
                        </div>
                        <p className="leading-6 text-slate-700">{attempt.reflection}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="还没有尝试记录"
                    description="完成、跳过或标记太难后，这里会形成记录。"
                  />
                )}
              </Panel>
            </>
          ) : (
            <Panel title="练习阶梯">
              <EmptyState
                title="还没有练习阶梯"
                description="创建计划后，这里会显示任务、来源和自适应建议。"
              />
            </Panel>
          )}
        </div>
      </div>
    </AuthGuard>
  );
}

type CreatePlanRequest = {
  kind: "create_plan";
  userId: string;
  targetScenario: string;
  anxietyLevel: number;
  previousAttempts: string[];
};

type CompleteTaskRequest = {
  kind: "complete_task";
  userId: string;
  taskId: string;
  status: "completed" | "skipped" | "too_hard";
  anxietyBefore: number;
  anxietyAfter: number;
  reflection: string;
};

type PendingExposureConsent = {
  detail: ConsentRequiredDetail;
  request: CreatePlanRequest | CompleteTaskRequest;
};

type RetryAction = {
  label: string;
  run: () => void;
};

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending_consent: "等待同意",
    active: "进行中",
    completed: "已完成",
    cancelled: "已取消",
    blocked: "已阻断",
    paused: "已暂停"
  };
  return labels[status] ?? status;
}

function isLinkedExposurePlan(plan: InterventionPlanView) {
  return plan.timeline.some((step) => step.skill === "exposure_planning_skill");
}
