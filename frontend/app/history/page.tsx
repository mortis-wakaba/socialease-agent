"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import type {
  InterventionPlanView,
  RoleplaySession,
  SessionReviewRecord,
  UserProfileResponse
} from "@/lib/types";
import { Badge, Button, EmptyState, ErrorBox, PageHeader, Panel } from "@/components/ui";

export default function HistoryPage() {
  const auth = useRequireAuth();
  const userId = auth.userId;
  const [plans, setPlans] = useState<InterventionPlanView[]>([]);
  const [roleplaySessions, setRoleplaySessions] = useState<RoleplaySession[]>([]);
  const [reviews, setReviews] = useState<SessionReviewRecord[]>([]);
  const [profile, setProfile] = useState<UserProfileResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canRetryLoad, setCanRetryLoad] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !userId) {
      return;
    }
    void loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, userId]);

  async function loadHistory() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setCanRetryLoad(false);
    try {
      const [planResult, roleplayResult, profileResult, reviewResult] = await Promise.all([
        api.listInterventionPlans(userId, 20),
        api.listRoleplaySessions(userId, 10),
        api.getUserProfile(userId),
        api.listSessionReviews(userId, 10)
      ]);
      setPlans(planResult.plans);
      setRoleplaySessions(roleplayResult.sessions);
      setProfile(profileResult);
      setReviews(reviewResult.reviews);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法载入练习历史");
      setCanRetryLoad(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="练习历史"
        description="查看最近的练习计划、结构化复盘、练习统计和可以继续的任务入口。"
      />
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <Panel title="当前用户">
            <div className="space-y-3 text-sm leading-6 text-slate-700">
              <Badge tone="info">{userId ?? "未登录"}</Badge>
              {profile ? (
                <dl className="grid grid-cols-2 gap-2 rounded-md border border-line bg-white p-3">
                  <Metric label="角色扮演" value={profile.practice_summary.roleplay_session_count} />
                  <Metric label="Worksheet" value={profile.practice_summary.worksheet_count} />
                  <Metric label="练习反馈" value={profile.practice_summary.exposure_attempt_count} />
                  <Metric
                    label="最近焦虑"
                    value={profile.practice_summary.latest_anxiety_level ?? "-"}
                  />
                </dl>
              ) : null}
              <Button type="button" variant="secondary" onClick={loadHistory} disabled={loading}>
                刷新历史
              </Button>
            </div>
          </Panel>

          <Panel title="下一步">
            <div className="space-y-2 text-sm leading-6 text-slate-700">
              <Link
                href="/practice"
                className="block rounded-md border border-line px-3 py-2 hover:border-brand"
              >
                开始新的角色扮演
              </Link>
              <Link
                href="/progress"
                className="block rounded-md border border-line px-3 py-2 hover:border-brand"
              >
                创建或继续社交练习计划
              </Link>
              <Link
                href="/settings"
                className="block rounded-md border border-line px-3 py-2 hover:border-brand"
              >
                管理隐私和数据
              </Link>
            </div>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="最近复盘">
            {reviews.length === 0 ? (
              <EmptyState
                title={loading ? "正在载入" : "暂无复盘记录"}
                description="完成角色扮演、结构化反思或社交练习后，可以保存低敏结构化复盘。"
              />
            ) : (
              <div className="space-y-3">
                {reviews.map((review) => (
                  <div
                    key={review.review_id}
                    className="rounded-md border border-line bg-white p-3 text-sm"
                  >
                    <div className="mb-2 flex flex-wrap gap-2">
                      <Badge tone="info">{review.source}</Badge>
                      <Badge tone={review.completed === "completed" ? "good" : review.completed === "pause" ? "warn" : "neutral"}>
                        {reviewCompletionLabel(review.completed)}
                      </Badge>
                      <Badge>
                        焦虑 {review.anxiety_before} → {review.anxiety_after}
                      </Badge>
                    </div>
                    <p className="leading-6 text-slate-700">
                      {review.next_step_summary}
                    </p>
                    <div className="mt-2 text-xs text-slate-500">
                      {new Date(review.created_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="最近角色扮演">
            {roleplaySessions.length === 0 ? (
              <EmptyState
                title={loading ? "正在载入" : "暂无角色扮演"}
                description="从角色扮演练习开始后，这里会显示最近会话和暂停状态。"
              />
            ) : (
              <div className="space-y-3">
                {roleplaySessions.map((session) => (
                  <div
                    key={session.session_id}
                    className="rounded-md border border-line bg-white p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="font-medium text-ink">
                          {roleplayScenarioLabel(session.scenario)}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                          {new Date(session.updated_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Badge tone={roleplayStatusTone(session.status)}>
                          {roleplayStatusLabel(session.status)}
                        </Badge>
                        <Badge>{session.messages.length} 条消息</Badge>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Link
                        href={`/practice?session_id=${encodeURIComponent(session.session_id)}`}
                        className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
                      >
                        查看会话
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="最近计划">
            <ErrorBox
              message={error}
              onRetry={canRetryLoad ? () => void loadHistory() : undefined}
              retrying={loading}
            />
            {plans.length === 0 ? (
              <EmptyState
                title={loading ? "正在载入" : "暂无练习历史"}
                description="从对话、角色扮演或社交练习计划开始后，这里会显示最近的练习计划。"
              />
            ) : (
              <div className="space-y-3">
                {plans.map((plan) => (
                  <div
                    key={plan.plan_id}
                    className="rounded-md border border-line bg-white p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="font-medium text-ink">{plan.timeline[0]?.title ?? plan.plan_id}</div>
                        <div className="mt-1 text-xs text-slate-500">
                          {new Date(plan.updated_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Badge tone={statusTone(plan.status)}>{statusLabel(plan.status)}</Badge>
                        <Badge>{plan.completed_steps}/{plan.total_steps}</Badge>
                      </div>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full bg-brand"
                        style={{ width: `${Math.round(plan.progress_ratio * 100)}%` }}
                      />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Link
                        href={`/progress?plan_id=${encodeURIComponent(plan.plan_id)}`}
                        className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
                      >
                        查看计划
                      </Link>
                      {plan.session_id && !isExposurePlan(plan) ? (
                        <Link
                          href={`/practice?session_id=${encodeURIComponent(plan.session_id)}`}
                          className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
                        >
                          继续练习
                        </Link>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
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

function statusTone(status: string) {
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

function reviewCompletionLabel(status: SessionReviewRecord["completed"]) {
  const labels: Record<SessionReviewRecord["completed"], string> = {
    completed: "已完成",
    partial: "部分完成",
    pause: "已暂停"
  };
  return labels[status];
}

function isExposurePlan(plan: InterventionPlanView) {
  return plan.timeline.some((step) => step.skill === "exposure_planning_skill");
}

function roleplayStatusTone(status: RoleplaySession["status"]) {
  if (status === "completed") {
    return "good" as const;
  }
  if (status === "paused") {
    return "warn" as const;
  }
  return "info" as const;
}

function roleplayStatusLabel(status: RoleplaySession["status"]) {
  const labels: Record<RoleplaySession["status"], string> = {
    active: "进行中",
    paused: "已暂停",
    completed: "已完成"
  };
  return labels[status];
}

function roleplayScenarioLabel(scenario: RoleplaySession["scenario"]) {
  const labels: Record<RoleplaySession["scenario"], string> = {
    classroom_speech: "课堂发言",
    group_discussion: "小组讨论",
    dorm_conflict: "宿舍沟通",
    club_icebreaking: "社团破冰",
    invite_classmate_meal: "约同学吃饭",
    ask_teacher_question: "向老师提问",
    interview_self_intro: "面试介绍",
    refuse_request: "拒绝请求",
    express_disagreement: "表达不同意见"
  };
  return labels[scenario] ?? scenario;
}
