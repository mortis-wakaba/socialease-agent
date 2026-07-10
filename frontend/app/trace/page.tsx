"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useDeveloperAccess } from "@/lib/use-developer-access";
import { AuthGuard } from "@/components/auth-guard";
import { DeveloperGuard } from "@/components/developer-guard";
import type {
  InterventionPlanView,
  InterventionStepStatus,
  TraceRecord
} from "@/lib/types";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  LLMUsageBadge,
  PageHeader,
  Panel,
  TextInput,
  riskTone
} from "@/components/ui";

export default function TracePage() {
  const developerAccess = useDeveloperAccess();
  const [runId, setRunId] = useState("");
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [plan, setPlan] = useState<InterventionPlanView | null>(null);
  const [loading, setLoading] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [retryTraceId, setRetryTraceId] = useState<string | null>(null);
  const [retryPlanRecord, setRetryPlanRecord] = useState<TraceRecord | null>(null);

  const loadPlan = useCallback(async (record: TraceRecord) => {
    if (!record.intervention_plan_id) {
      return;
    }
    setPlanLoading(true);
    setPlanError(null);
    setRetryPlanRecord(null);
    try {
      const response = await api.getInterventionPlan(
        record.intervention_plan_id,
        record.user_id
      );
      setPlan(response.plan);
    } catch (err) {
      setPlanError(
        err instanceof Error ? err.message : "Intervention plan not found"
      );
      setPlan(null);
      setRetryPlanRecord(record);
    } finally {
      setPlanLoading(false);
    }
  }, []);

  const loadTrace = useCallback(async (nextRunId: string) => {
    setLoading(true);
    setError(null);
    setRetryTraceId(null);
    setPlan(null);
    setPlanError(null);
    try {
      const record = await api.getRun(nextRunId);
      setTrace(record);
      if (record.intervention_plan_id) {
        await loadPlan(record);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trace not found");
      setTrace(null);
      setRetryTraceId(nextRunId);
    } finally {
      setLoading(false);
    }
  }, [loadPlan]);

  useEffect(() => {
    if (!developerAccess.ready || !developerAccess.allowed) {
      return;
    }
    const queryRunId = new URLSearchParams(window.location.search).get("run_id");
    if (!queryRunId) {
      return;
    }
    setRunId(queryRunId);
    void loadTrace(queryRunId);
  }, [developerAccess.ready, developerAccess.allowed, loadTrace]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!developerAccess.ready || !developerAccess.allowed) {
      setError("需要开发者权限后才能加载 trace。");
      return;
    }
    if (!runId.trim()) {
      setError("Please paste a run_id before loading a trace.");
      return;
    }
    await loadTrace(runId.trim());
  }

  const steps = trace
    ? [
        {
          title: "Safety",
          body: trace.safety_result.reason,
          badge: (
            <div className="flex flex-wrap gap-2">
              <Badge tone={riskTone(trace.safety_result.risk_level)}>
                {trace.safety_result.risk_level}
              </Badge>
              <LLMUsageBadge usage={trace.safety_result.llm_usage} />
            </div>
          )
        },
        {
          title: "Router",
          body: trace.intent_result.reason,
          badge: (
            <div className="flex flex-wrap gap-2">
              <Badge tone="info">{trace.intent_result.intent}</Badge>
              <LLMUsageBadge usage={trace.intent_result.llm_usage} />
            </div>
          )
        },
        {
          title: "Agent",
          body: `Selected agent: ${trace.selected_agent}`,
          badge: <Badge>{trace.latency_ms.toFixed(1)} ms</Badge>
        },
        {
          title: "Memory",
          body: trace.privacy_summary.fields
            .map((field) => {
              const redacted = field.redacted_types.length
                ? ` redacted: ${field.redacted_types.join(", ")}`
                : " no identifiers redacted";
              const minimized = field.minimized ? "minimized" : "retained with policy";
              return `${field.field}: ${minimized};${redacted}`;
            })
            .join("\n"),
          badge: <Badge tone={trace.product_safe ? "good" : "warn"}>{trace.privacy_summary.trace_layer}</Badge>
        },
        {
          title: "Output",
          body: trace.output,
          badge: <Badge tone="good">complete</Badge>
        }
      ]
    : [];

  return (
    <AuthGuard>
      <DeveloperGuard>
        <PageHeader
          title="Trace"
          description="Inspect one saved agent run as Safety → Router → Agent → Memory → Output."
        />
        <Panel title="Lookup">
          <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row">
            <TextInput
              value={runId}
              onChange={(event) => setRunId(event.target.value)}
              placeholder="Paste run_id"
            />
            <Button type="submit" disabled={loading}>
              {loading ? "Loading..." : "Load Trace"}
            </Button>
          </form>
          <div className="mt-3">
            <ErrorBox
              message={error}
              onRetry={retryTraceId ? () => void loadTrace(retryTraceId) : undefined}
              retrying={loading}
            />
          </div>
        </Panel>

        {trace && (
          <div className="mt-4 grid gap-4 lg:grid-cols-[280px_1fr]">
          <Panel title="Run">
            <div className="space-y-3 text-sm text-slate-700">
              <div>
                <div className="text-xs font-medium uppercase text-slate-500">run_id</div>
                <div className="break-all">{trace.run_id}</div>
              </div>
              <div>
                <div className="text-xs font-medium uppercase text-slate-500">user</div>
                <div>{trace.user_id}</div>
              </div>
              <div>
                <div className="text-xs font-medium uppercase text-slate-500">input</div>
                <div>{trace.input}</div>
              </div>
              <div>
                <div className="text-xs font-medium uppercase text-slate-500">privacy</div>
                <div>{trace.product_safe ? "Product-safe trace" : "Restricted trace"}</div>
              </div>
              {trace.intervention_plan_id ? (
                <div>
                  <div className="text-xs font-medium uppercase text-slate-500">
                    intervention plan
                  </div>
                  <div className="break-all">{trace.intervention_plan_id}</div>
                </div>
              ) : null}
            </div>
          </Panel>
          <div className="space-y-4">
            <Panel title="Workflow">
              <div className="space-y-3">
                {steps.map((step) => (
                  <div key={step.title} className="rounded-md border border-line p-3">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="font-medium text-ink">{step.title}</div>
                      {step.badge}
                    </div>
                    <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                      {step.body}
                    </p>
                  </div>
                ))}
              </div>
            </Panel>
            {trace.intent_result.intent === "roleplay_practice" ? (
              <RoleplayRubricTraceHint />
            ) : null}
            {trace.intervention_plan_id ? (
              <Panel
                title="Intervention Plan"
                action={
                  plan ? (
                    <Badge tone={planStatusTone(plan.status)}>
                      {plan.completed_steps}/{plan.total_steps}
                    </Badge>
                  ) : null
                }
              >
                {planLoading ? (
                  <p className="text-sm text-slate-500">Loading plan timeline...</p>
                ) : null}
                <ErrorBox
                  message={planError}
                  onRetry={
                    retryPlanRecord
                      ? () => void loadPlan(retryPlanRecord)
                      : undefined
                  }
                  retrying={planLoading}
                />
                {plan ? <InterventionPlanTimeline plan={plan} /> : null}
              </Panel>
            ) : null}
          </div>
          </div>
        )}
        {!trace && !error && (
          <div className="mt-4">
            <EmptyState
              title="No trace loaded"
              description="Run a chat message first, then paste the run_id here to inspect the workflow."
            />
          </div>
        )}
      </DeveloperGuard>
    </AuthGuard>
  );
}

function RoleplayRubricTraceHint() {
  const signals = [
    "reason markers",
    "request markers",
    "boundary markers",
    "empathy markers",
    "specificity markers",
    "collaborative markers"
  ];

  return (
    <Panel
      title="Roleplay Feedback Path"
      action={<Badge tone="info">privacy-safe rubric</Badge>}
    >
      <div className="space-y-3 text-sm leading-6 text-slate-700">
        <p>
          This trace shows the harness path for a role-play intent. The direct
          feedback view in Practice uses derived, non-verbatim signals instead
          of stored raw practice text.
        </p>
        <div className="flex flex-wrap gap-2">
          {signals.map((signal) => (
            <Badge key={signal} tone="neutral">
              {signal}
            </Badge>
          ))}
        </div>
        <p className="text-xs leading-5 text-slate-500">
          Open Practice feedback to inspect the per-dimension rubric breakdown:
          clarity, naturalness, assertiveness, and empathy.
        </p>
      </div>
    </Panel>
  );
}

function InterventionPlanTimeline({ plan }: { plan: InterventionPlanView }) {
  const percent = Math.round(plan.progress_ratio * 100);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 text-sm text-slate-700 md:grid-cols-3">
        <div>
          <div className="text-xs font-medium uppercase text-slate-500">status</div>
          <Badge tone={planStatusTone(plan.status)}>{planStatusLabel(plan.status)}</Badge>
        </div>
        <div>
          <div className="text-xs font-medium uppercase text-slate-500">progress</div>
          <div>{percent}% complete</div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase text-slate-500">session</div>
          <div className="break-all">{plan.session_id}</div>
        </div>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full bg-brand"
          style={{ width: `${Math.max(4, percent)}%` }}
        />
      </div>
      <div className="space-y-3">
        {plan.timeline.map((step) => (
          <div
            key={step.step_id}
            className={`rounded-md border p-3 ${
              step.is_current
                ? "border-brand bg-emerald-50"
                : "border-line bg-white"
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="flex h-7 w-7 items-center justify-center rounded-full border border-line bg-white text-xs font-semibold text-slate-600">
                  {step.order}
                </span>
                <div>
                  <div className="font-medium text-ink">{step.title}</div>
                  <div className="text-xs text-slate-500">{step.skill}</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge tone={stepStatusTone(step.status)}>{step.status}</Badge>
                {step.requires_consent ? <Badge tone="warn">consent</Badge> : null}
                {step.intensity ? <Badge>intensity {step.intensity}</Badge> : null}
                {step.is_current ? <Badge tone="info">current</Badge> : null}
              </div>
            </div>
            {step.result_summary ? (
              <p className="mt-2 text-sm leading-6 text-slate-700">
                {step.result_summary}
              </p>
            ) : null}
            {step.stop_condition ? (
              <p className="mt-2 text-xs leading-5 text-slate-500">
                Stop condition: {step.stop_condition}
              </p>
            ) : null}
            {step.protocol_id ? (
              <p className="mt-2 break-all text-xs text-slate-500">
                protocol: {step.protocol_id}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function planStatusTone(status: InterventionPlanView["status"]) {
  if (status === "completed") {
    return "good" as const;
  }
  if (status === "pending_consent" || status === "paused") {
    return "warn" as const;
  }
  if (status === "blocked" || status === "cancelled") {
    return "danger" as const;
  }
  return "info" as const;
}

function planStatusLabel(status: InterventionPlanView["status"]) {
  const labels: Record<InterventionPlanView["status"], string> = {
    pending_consent: "等待同意",
    active: "进行中",
    completed: "已完成",
    cancelled: "已取消",
    blocked: "已阻断",
    paused: "已暂停"
  };
  return labels[status];
}

function stepStatusTone(status: InterventionStepStatus) {
  if (status === "completed") {
    return "good" as const;
  }
  if (status === "in_progress" || status === "pending") {
    return "warn" as const;
  }
  if (status === "blocked" || status === "cancelled") {
    return "danger" as const;
  }
  return "neutral" as const;
}
