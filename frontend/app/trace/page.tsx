"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import type { TraceRecord } from "@/lib/types";
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
  const [runId, setRunId] = useState("");
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!runId.trim()) {
      setError("Please paste a run_id before loading a trace.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setTrace(await api.getRun(runId.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trace not found");
      setTrace(null);
    } finally {
      setLoading(false);
    }
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
          body: "Trace saved for this run. Practice-specific memory is updated only by role-play, worksheet, and exposure APIs.",
          badge: <Badge tone="info">persisted</Badge>
        },
        {
          title: "Output",
          body: trace.output,
          badge: <Badge tone="good">complete</Badge>
        }
      ]
    : [];

  return (
    <>
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
          <ErrorBox message={error} />
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
            </div>
          </Panel>
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
    </>
  );
}
