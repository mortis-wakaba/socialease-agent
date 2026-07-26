"use client";

import { useEffect, useState } from "react";
import { AuthGuard } from "@/components/auth-guard";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea
} from "@/components/ui";
import { api } from "@/lib/api";
import type {
  EpisodicMemoryView,
  MemoryCenterResponse,
  MemoryDoctorIssue,
  MemoryDoctorReport,
  MemoryProposalView,
  MemoryType
} from "@/lib/types";
import { useRequireAuth } from "@/lib/use-require-auth";

const typeLabels: Record<string, string> = {
  practice_experience: "练习经历",
  helpful_strategy: "有用策略",
  practice_milestone: "练习里程碑",
  social_context: "社交情境",
  recurring_pattern: "重复模式"
};

const statusLabels: Record<string, string> = {
  active: "使用中",
  inactive: "已停用",
  archived: "已归档",
  superseded: "已替代",
  revoked: "已撤回",
  paused: "已暂停"
};

const sourceLabels: Record<string, string> = {
  chat: "对话中的明确表达",
  roleplay: "角色扮演",
  worksheet: "结构化反思",
  exposure: "分级练习",
  session_review: "练习复盘",
  user_confirmed: "由你确认"
};

const reasonLabels: Record<string, string> = {
  user_confirmed_proposal: "你确认了候选内容",
  completed_practice_allowed: "完成了一次产品内练习",
  helpful_strategy_allowed: "你明确表示这个策略有帮助",
  social_context_confirmation_required: "社交情境需要你确认",
  explicit_experience_confirmation_required: "练习经历需要你确认",
  general_consent_required: "未来个性化授权需要确认",
  unknown: "历史版本未记录原因"
};

const doctorIssueLabels: Record<string, string> = {
  duplicate_memory: "可能存在重复记忆",
  conflicting_memory: "可能存在相互冲突的记忆",
  stale_unused_memory: "有长期未使用的记忆",
  consent_inactive_memory: "授权关闭后仍保留有记忆",
  type_personalization_disabled: "已关闭类别仍保留有记录",
  source_reference_missing: "有记忆缺少来源引用",
  timestamp_invalid: "有记忆时间异常",
  orphan_embedding: "向量索引存在孤立项",
  active_memory_over_budget: "活动记忆包可能超过预算",
  stale_checkpoint: "有长期未活动的练习线程",
  pending_proposal_aged: "有候选记忆等待确认时间较长"
};

const doctorRecommendationLabels: Record<string, string> = {
  review_duplicate_memories: "比较后归档或删除不再需要的重复项。",
  review_conflicting_memories: "确认当前有效内容，再手动编辑或归档旧记录。",
  consider_archiving_stale_memory: "如果不再需要，可在下方归档。",
  review_retained_memories_after_consent:
    "关闭授权不会自动删除；请确认是否保留或手动清除。",
  review_disabled_type_records:
    "类别关闭后不会用于个性化；仍可按需归档或删除已有记录。",
  review_memory_source: "确认该记录是否仍应保留。",
  review_memory_timestamp: "检查记录时间，必要时删除并重新保存。",
  rebuild_embedding_index: "重新构建启用中的向量索引。",
  review_active_memory_budget: "减少活动记忆或归档不再需要的练习线程。",
  review_stale_checkpoint: "确认是否继续该练习，或将其结束归档。",
  review_pending_proposal: "确认保存或拒绝并清除候选内容。"
};

export default function MemoryPage() {
  const auth = useRequireAuth();
  const userId = auth.userId;
  const [center, setCenter] = useState<MemoryCenterResponse | null>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (auth.ready && auth.authenticated && userId) {
      void load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, userId]);

  async function load() {
    if (!userId) return;
    setError(null);
    try {
      const result = await api.getMemoryCenter(userId);
      setCenter(result);
      setEditing(
        Object.fromEntries(
          result.memories.map((memory) => [memory.memory_id, memory.summary])
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法载入记忆中心");
    }
  }

  async function mutate(
    itemId: string,
    action: () => Promise<unknown>,
    successMessage: string
  ) {
    setBusyId(itemId);
    setError(null);
    setMessage(null);
    try {
      await action();
      await load();
      setMessage(successMessage);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "操作失败，请刷新后重试"
      );
    } finally {
      setBusyId(null);
    }
  }

  if (!auth.ready || !auth.authenticated || !userId) {
    return (
      <AuthGuard>
        <EmptyState
          title="正在检查登录状态"
          description="登录后才能查看本人记忆。"
        />
      </AuthGuard>
    );
  }

  return (
    <AuthGuard>
      <PageHeader
        title="记忆中心"
        description="查看 Agent 可能用于未来个性化的内容，并分别管理稳定设置、当前练习线程和情节记忆。"
      />

      <ErrorBox
        message={error}
        onRetry={() => void load()}
        retrying={busyId !== null}
      />
      {message ? (
        <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {message}
        </div>
      ) : null}

      {!center ? (
        <EmptyState
          title="正在载入记忆"
          description="如果长时间没有结果，可以点击错误提示中的重试。"
        />
      ) : (
        <div className="space-y-4">
          <Panel title="Agent Memory 与聊天历史">
            <p className="text-sm leading-6 text-slate-700">
              {center.memory_history_distinction}
            </p>
          </Panel>

          <MemoryDoctorPanel report={center.doctor} />

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="稳定设置">
              <dl className="space-y-2 text-sm">
                <MemoryRow
                  label="历史摘要个性化"
                  value={
                    center.stable_memory.consent_state
                      .consent_to_practice_summary
                      ? "已允许"
                      : "未允许"
                  }
                />
                <MemoryRow
                  label="长期练习偏好"
                  value={
                    center.stable_memory.consent_state
                      .consent_to_save_preferences
                      ? "已开启"
                      : "未开启"
                  }
                />
                <MemoryRow
                  label="主要目标"
                  value={
                    center.stable_memory.onboarding_profile.primary_goal ??
                    "未设置"
                  }
                />
                <MemoryRow
                  label="反馈方式"
                  value={
                    center.stable_memory.practice_preferences
                      .preferred_feedback_style ?? "未设置"
                  }
                />
              </dl>
            </Panel>

            <Panel title="当前练习线程">
              {center.active_threads.length === 0 ? (
                <EmptyState
                  title="没有活动线程"
                  description="暂停或进行中的长期练习会显示在这里。"
                />
              ) : (
                <div className="space-y-2">
                  {center.active_threads.map((thread) => (
                    <div
                      key={thread.thread_id}
                      className="rounded-md border border-line p-3 text-sm"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-ink">
                          {thread.current_scenario ?? "练习线程"}
                        </span>
                        <Badge tone={thread.status === "paused" ? "warn" : "good"}>
                          {statusLabels[thread.status] ?? thread.status}
                        </Badge>
                      </div>
                      <p className="mt-2 text-slate-600">
                        {thread.unresolved_next_step ?? "暂无待完成步骤"}
                      </p>
                      <p className="mt-2 text-xs text-slate-500">
                        最近活动：{formatDate(thread.last_activity_at)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>

          <Panel title="按类别控制未来个性化">
            <p className="mb-3 text-sm leading-6 text-slate-600">
              关闭某一类后，已有记录仍可查看和删除，但不会被检索进入未来 Agent
              上下文，也不会新增该类 Agent Memory。
            </p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(typeLabels).map(([memoryType, label]) => {
                const disabled =
                  center.stable_memory.disabled_memory_types.includes(
                    memoryType as MemoryType
                  );
                return (
                  <Button
                    key={memoryType}
                    variant={disabled ? "secondary" : "primary"}
                    disabled={busyId === `type:${memoryType}`}
                    onClick={() =>
                      void mutate(
                        `type:${memoryType}`,
                        () =>
                          api.updateMemoryTypePersonalization(
                            userId,
                            memoryType as MemoryType,
                            disabled
                          ),
                        disabled
                          ? `已重新允许“${label}”用于未来个性化。`
                          : `已停止“${label}”用于未来个性化。`
                      )
                    }
                  >
                    {label}：{disabled ? "已关闭" : "已允许"}
                  </Button>
                );
              })}
            </div>
          </Panel>

          <Panel title={`待确认候选（${center.pending_proposals.length}）`}>
            {center.pending_proposals.length === 0 ? (
              <EmptyState
                title="没有待确认内容"
                description="需要你明确决定的候选记忆会显示在这里；模型不能自行确认。"
              />
            ) : (
              <div className="space-y-3">
                {center.pending_proposals.map((proposal) => (
                  <ProposalCard
                    key={proposal.proposal_id}
                    proposal={proposal}
                    busy={busyId === proposal.proposal_id}
                    onDecision={(decision) =>
                      void mutate(
                        proposal.proposal_id,
                        () =>
                          api.decideMemoryProposal(
                            userId,
                            proposal.proposal_id,
                            decision,
                            proposal.version
                          ),
                        decision === "confirm"
                          ? "已确认并保存这条记忆。"
                          : "已拒绝并清除候选内容。"
                      )
                    }
                  />
                ))}
              </div>
            )}
          </Panel>

          <Panel title={`情节记忆（${center.memories.length}）`}>
            {center.memories.length === 0 ? (
              <EmptyState
                title="还没有情节记忆"
                description="完成练习或明确确认后，低敏摘要才可能出现在这里。"
              />
            ) : (
              <div className="space-y-4">
                {center.memories.map((memory) => (
                  <div
                    key={memory.memory_id}
                    className="rounded-md border border-line p-4"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="info">
                        {typeLabels[memory.memory_type] ?? memory.memory_type}
                      </Badge>
                      <Badge
                        tone={
                          memory.status === "active"
                            ? "good"
                            : memory.status === "archived"
                              ? "warn"
                              : "neutral"
                        }
                      >
                        {statusLabels[memory.status] ?? memory.status}
                      </Badge>
                      <span className="text-xs text-slate-500">
                        来源：
                        {sourceLabels[memory.source_type] ?? memory.source_type} ·
                        保存原因：
                        {reasonLabels[memory.saved_reason] ?? memory.saved_reason}
                      </span>
                    </div>
                    <TextArea
                      className="mt-3"
                      value={editing[memory.memory_id] ?? memory.summary}
                      disabled={
                        busyId === memory.memory_id ||
                        ["superseded", "revoked"].includes(memory.status)
                      }
                      onChange={(event) =>
                        setEditing((current) => ({
                          ...current,
                          [memory.memory_id]: event.target.value
                        }))
                      }
                    />
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        disabled={
                          busyId === memory.memory_id ||
                          (editing[memory.memory_id] ?? "").trim() ===
                            memory.summary
                        }
                        onClick={() =>
                          void mutate(
                            memory.memory_id,
                            () =>
                              api.editMemory(
                                userId,
                                memory.memory_id,
                                (editing[memory.memory_id] ?? "").trim(),
                                memory.version
                              ),
                            "已更新记忆摘要。"
                          )
                        }
                      >
                        保存修改
                      </Button>
                      {memory.status === "active" ||
                      memory.status === "inactive" ? (
                        <Button
                          variant="secondary"
                          disabled={busyId === memory.memory_id}
                          onClick={() =>
                            void mutate(
                              memory.memory_id,
                              () =>
                                api.archiveMemory(
                                  userId,
                                  memory.memory_id,
                                  memory.version
                                ),
                              "已归档；普通检索将不再使用这条记忆。"
                            )
                          }
                        >
                          归档
                        </Button>
                      ) : null}
                      {memory.status === "archived" ? (
                        <Button
                          variant="secondary"
                          disabled={busyId === memory.memory_id}
                          onClick={() =>
                            void mutate(
                              memory.memory_id,
                              () =>
                                api.restoreMemory(
                                  userId,
                                  memory.memory_id,
                                  memory.version
                                ),
                              "已恢复为可用记忆。"
                            )
                          }
                        >
                          恢复
                        </Button>
                      ) : null}
                      <Button
                        variant="danger"
                        disabled={busyId === memory.memory_id}
                        onClick={() => {
                          if (
                            window.confirm(
                              "确认永久删除这条记忆正文？此操作不可恢复。"
                            )
                          ) {
                            void mutate(
                              memory.memory_id,
                              () =>
                                api.deleteMemoryItem(
                                  userId,
                                  memory.memory_id,
                                  memory.version
                                ),
                              "已永久删除记忆正文。"
                            );
                          }
                        }}
                      >
                        删除
                      </Button>
                    </div>
                    <p className="mt-3 text-xs text-slate-500">
                      发生时间：{formatDate(memory.occurred_at)} · 版本：
                      {memory.version}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      )}
    </AuthGuard>
  );
}

function MemoryDoctorPanel({ report }: { report: MemoryDoctorReport }) {
  const checked = report.checks.filter(
    (check) => check.status !== "not_applicable"
  ).length;
  const unavailable = report.checks.filter(
    (check) => check.status === "not_applicable"
  );
  return (
    <Panel
      title="Memory Doctor（只读检查）"
      action={
        <Badge tone={report.issues.length > 0 ? "warn" : "good"}>
          {report.issues.length > 0
            ? `发现 ${report.issues.length} 项`
            : "未发现问题"}
        </Badge>
      }
    >
      <p className="text-sm leading-6 text-slate-600">
        已运行 {checked} 项确定性检查。报告不包含记忆正文，也没有自动修改任何内容；
        是否编辑、归档或删除仍由你决定。
      </p>
      {unavailable.length > 0 ? (
        <p className="mt-2 text-xs text-slate-500">
          {unavailable
            .map((check) =>
              check.code === "orphan_embedding"
                ? "向量索引完整性：当前未启用，因此不适用"
                : `${doctorIssueLabels[check.code] ?? check.code}：不适用`
            )
            .join("；")}
        </p>
      ) : null}
      {report.issues_truncated ? (
        <p className="mt-2 text-xs text-amber-700">
          问题数量超过单次报告上限；请先处理已显示项目，再重新运行检查。
        </p>
      ) : null}
      {report.issues.length === 0 ? (
        <div className="mt-3">
          <EmptyState
            title="当前未发现需要处理的问题"
            description="这是一次只读质量检查，不代表对记忆正文作医疗或事实判断。"
          />
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          {report.issues.map((issue) => (
            <DoctorIssueRow key={issue.issue_id} issue={issue} />
          ))}
        </div>
      )}
      <p className="mt-3 text-xs text-slate-500">
        检查时间：{formatDate(report.generated_at)}
      </p>
    </Panel>
  );
}

function DoctorIssueRow({ issue }: { issue: MemoryDoctorIssue }) {
  const tone =
    issue.severity === "action_required"
      ? "danger"
      : issue.severity === "warning"
        ? "warn"
        : "info";
  return (
    <div className="rounded-md border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>
          {issue.severity === "action_required"
            ? "需要确认"
            : issue.severity === "warning"
              ? "建议检查"
              : "提醒"}
        </Badge>
        <span className="font-medium text-ink">
          {doctorIssueLabels[issue.code] ?? issue.code}
        </span>
        <span className="text-xs text-slate-500">
          影响 {issue.affected_count} 项
        </span>
      </div>
      <p className="mt-2 leading-6 text-slate-600">
        {doctorRecommendationLabels[issue.recommendation_code] ??
          "请在下方逐项查看后决定是否处理。"}
      </p>
    </div>
  );
}

function ProposalCard({
  proposal,
  busy,
  onDecision
}: {
  proposal: MemoryProposalView;
  busy: boolean;
  onDecision: (decision: "confirm" | "reject") => void;
}) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="warn">
          {typeLabels[proposal.memory_type] ?? proposal.memory_type}
        </Badge>
        <span className="text-xs text-slate-600">
          原因：
          {reasonLabels[proposal.saved_reason] ?? proposal.saved_reason} · 到期：
          {formatDate(proposal.expires_at)}
        </span>
      </div>
      <p className="mt-2 leading-6 text-slate-800">{proposal.summary}</p>
      <div className="mt-3 flex gap-2">
        <Button disabled={busy} onClick={() => onDecision("confirm")}>
          确认保存
        </Button>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() => onDecision("reject")}
        >
          拒绝并清除
        </Button>
      </div>
    </div>
  );
}

function MemoryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-line px-3 py-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-ink">{value}</dd>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
