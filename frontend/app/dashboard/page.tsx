"use client";

import Link from "next/link";
import type React from "react";
import { useEffect, useMemo, useState } from "react";
import { AuthGuard } from "@/components/auth-guard";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  PageHeader,
  Panel
} from "@/components/ui";
import { api } from "@/lib/api";
import { useRequireAuth } from "@/lib/use-require-auth";
import type {
  InterventionPlanView,
  SessionReviewRecord,
  UserOnboardingProfile,
  UserProfileResponse
} from "@/lib/types";

export default function DashboardPage() {
  const auth = useRequireAuth();
  const userId = auth.userId;
  const [profile, setProfile] = useState<UserProfileResponse | null>(null);
  const [onboarding, setOnboarding] = useState<UserOnboardingProfile | null>(null);
  const [plans, setPlans] = useState<InterventionPlanView[]>([]);
  const [reviews, setReviews] = useState<SessionReviewRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !userId) {
      return;
    }
    void loadDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, userId]);

  async function loadDashboard() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [profileResult, onboardingResult, planResult, reviewResult] =
        await Promise.all([
          api.getUserProfile(userId),
          api.getOnboardingProfile(userId),
          api.listInterventionPlans(userId, 5),
          api.listSessionReviews(userId, 5)
        ]);
      setProfile(profileResult);
      setOnboarding(onboardingResult.onboarding_profile);
      setPlans(planResult.plans);
      setReviews(reviewResult.reviews);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法载入工作台");
    } finally {
      setLoading(false);
    }
  }

  const currentPlan = useMemo(
    () =>
      plans.find((plan) => plan.status === "active" || plan.status === "pending_consent") ??
      plans.find((plan) => plan.status === "paused") ??
      plans[0] ??
      null,
    [plans]
  );
  const latestReview = reviews[0] ?? null;
  const nextStep = nextStepSuggestion({
    onboarding,
    currentPlan,
    latestReview,
    profile
  });

  return (
    <AuthGuard>
      <PageHeader
        title="练习工作台"
        description="从这里查看当前状态、最近复盘和下一步练习。页面只展示产品状态，不展示内部风险评分、路由原因或 trace 诊断。"
      />

      <ErrorBox
        message={error}
        onRetry={() => void loadDashboard()}
        retrying={loading}
      />

      <div className="mt-4 grid gap-4 lg:grid-cols-[340px_1fr]">
        <div className="space-y-4">
          <Panel
            title="当前状态"
            action={
              <Button type="button" variant="secondary" onClick={() => void loadDashboard()} disabled={loading}>
                {loading ? "刷新中..." : "刷新"}
              </Button>
            }
          >
            <div className="space-y-3 text-sm leading-6 text-slate-700">
              <div className="flex flex-wrap gap-2">
                <Badge tone={onboarding?.boundary_acknowledged ? "good" : "warn"}>
                  {onboarding?.boundary_acknowledged ? "已确认边界" : "待完成开始设置"}
                </Badge>
                {currentPlan ? (
                  <Badge tone={planTone(currentPlan.status)}>{statusLabel(currentPlan.status)}</Badge>
                ) : (
                  <Badge>暂无进行中计划</Badge>
                )}
              </div>
              <dl className="grid grid-cols-2 gap-2 rounded-md border border-line bg-panel p-3">
                <Metric
                  label="偏好场景"
                  value={scenarioLabel(onboarding?.preferred_scenario)}
                />
                <Metric
                  label="当前强度"
                  value={onboarding?.current_anxiety_level ?? "-"}
                />
                <Metric
                  label="角色扮演"
                  value={profile?.practice_summary.roleplay_session_count ?? "-"}
                />
                <Metric
                  label="复盘记录"
                  value={reviews.length}
                />
              </dl>
              {!onboarding?.boundary_acknowledged ? (
                <LinkButton href="/onboarding">完成开始前设置</LinkButton>
              ) : null}
            </div>
          </Panel>

          <Panel title="安全提醒">
            <p className="text-sm leading-6 text-slate-700">
              你可以随时暂停、降低难度或退出练习。SocialEase 只提供社交练习和自助反思，
              不做诊断，也不替代心理咨询。
            </p>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="建议下一步">
            <div className="space-y-3">
              <p className="text-sm leading-6 text-slate-700">{nextStep.message}</p>
              <div className="flex flex-wrap gap-2">
                {nextStep.href ? (
                  <LinkButton href={nextStep.href}>{nextStep.actionLabel}</LinkButton>
                ) : null}
                <LinkButton href="/history" secondary>
                  查看历史
                </LinkButton>
                <LinkButton href="/settings" secondary>
                  管理数据
                </LinkButton>
              </div>
            </div>
          </Panel>

          <Panel title="最近计划">
            {currentPlan ? (
              <PlanSummary plan={currentPlan} />
            ) : (
              <EmptyState
                title={loading ? "正在载入" : "还没有练习计划"}
                description="你可以从对话或社交练习计划页开始一个低强度练习。"
              />
            )}
          </Panel>

          <Panel title="最近复盘">
            {latestReview ? (
              <ReviewSummary review={latestReview} />
            ) : (
              <EmptyState
                title={loading ? "正在载入" : "还没有复盘"}
                description="完成练习后保存 30 秒结构化复盘，下一次计划会参考脱敏摘要。"
              />
            )}
          </Panel>
        </div>
      </div>
    </AuthGuard>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs uppercase text-slate-500">{label}</dt>
      <dd className="mt-1 truncate text-base font-semibold text-ink">{value}</dd>
    </div>
  );
}

function LinkButton({
  href,
  children,
  secondary = false
}: {
  href: string;
  children: React.ReactNode;
  secondary?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`rounded-md border px-3 py-2 text-sm font-medium ${
        secondary
          ? "border-line text-slate-700 hover:border-brand hover:text-brand"
          : "border-brand bg-brand text-white hover:bg-[#176052]"
      }`}
    >
      {children}
    </Link>
  );
}

function PlanSummary({ plan }: { plan: InterventionPlanView }) {
  const currentStep =
    plan.timeline.find((step) => step.is_current) ?? plan.timeline[0] ?? null;
  const canOpenPracticeSession = Boolean(plan.session_id) && !isExposurePlan(plan);
  return (
    <div className="space-y-3 text-sm leading-6 text-slate-700">
      <div className="flex flex-wrap gap-2">
        <Badge tone={planTone(plan.status)}>{statusLabel(plan.status)}</Badge>
        <Badge>{plan.completed_steps}/{plan.total_steps}</Badge>
      </div>
      <div>
        <div className="font-medium text-ink">
          {currentStep?.title ?? "当前计划"}
        </div>
        {currentStep?.result_summary ? (
          <p className="mt-1 text-slate-600">{currentStep.result_summary}</p>
        ) : null}
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full bg-brand"
          style={{ width: `${Math.round(plan.progress_ratio * 100)}%` }}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <LinkButton href={`/progress?plan_id=${encodeURIComponent(plan.plan_id)}`}>
          查看计划
        </LinkButton>
        {canOpenPracticeSession ? (
          <LinkButton href={`/practice?session_id=${encodeURIComponent(plan.session_id)}`} secondary>
            继续练习
          </LinkButton>
        ) : null}
      </div>
    </div>
  );
}

function ReviewSummary({ review }: { review: SessionReviewRecord }) {
  return (
    <div className="space-y-3 text-sm leading-6 text-slate-700">
      <div className="flex flex-wrap gap-2">
        <Badge tone="info">{review.source}</Badge>
        <Badge tone={review.completed === "completed" ? "good" : review.completed === "pause" ? "warn" : "neutral"}>
          {review.completed === "completed" ? "已完成" : review.completed === "pause" ? "已暂停" : "部分完成"}
        </Badge>
        <Badge>焦虑 {review.anxiety_before} → {review.anxiety_after}</Badge>
      </div>
      <p>{review.next_step_summary}</p>
      <div className="text-xs text-slate-500">
        {new Date(review.created_at).toLocaleString()}
      </div>
    </div>
  );
}

function nextStepSuggestion({
  onboarding,
  currentPlan,
  latestReview,
  profile
}: {
  onboarding: UserOnboardingProfile | null;
  currentPlan: InterventionPlanView | null;
  latestReview: SessionReviewRecord | null;
  profile: UserProfileResponse | null;
}) {
  if (!onboarding?.boundary_acknowledged) {
    return {
      message: "先完成开始前设置，确认非医疗边界、偏好场景和当前强度，再进入练习会更稳。",
      href: "/onboarding",
      actionLabel: "完成开始前设置"
    };
  }
  if (currentPlan?.status === "paused") {
    return {
      message: "上次练习处于暂停状态。可以先查看计划，选择继续、降低难度，或只做一次轻量复盘。",
      href: `/progress?plan_id=${encodeURIComponent(currentPlan.plan_id)}`,
      actionLabel: "查看暂停计划"
    };
  }
  if (currentPlan) {
    return {
      message: "你有一个可继续的练习计划。建议从当前步骤继续，并保留随时暂停的选项。",
      href: `/progress?plan_id=${encodeURIComponent(currentPlan.plan_id)}`,
      actionLabel: "继续当前计划"
    };
  }
  if (latestReview) {
    return {
      message: `可以参考最近复盘的下一步：${latestReview.next_step_summary}`,
      href: "/progress",
      actionLabel: "生成下一步计划"
    };
  }
  if ((profile?.practice_summary.roleplay_session_count ?? 0) > 0) {
    return {
      message: "你已经做过角色扮演，可以把其中一个场景拆成更小的社交练习阶梯。",
      href: "/progress",
      actionLabel: "创建练习阶梯"
    };
  }
  return {
    message: "可以从一个低强度场景开始，例如课堂发言开场白、宿舍沟通短句或邀请同学吃饭。",
    href: "/practice",
    actionLabel: "开始角色扮演"
  };
}

function scenarioLabel(value?: string | null) {
  return value || "-";
}

function planTone(status: string) {
  if (status === "completed") {
    return "good" as const;
  }
  if (status === "blocked" || status === "cancelled") {
    return "danger" as const;
  }
  if (status === "pending_consent" || status === "paused") {
    return "warn" as const;
  }
  return "info" as const;
}

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

function isExposurePlan(plan: InterventionPlanView) {
  return plan.timeline.some((step) => step.skill === "exposure_planning_skill");
}
