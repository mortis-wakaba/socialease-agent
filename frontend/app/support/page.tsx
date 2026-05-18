"use client";

import { FormEvent, useState } from "react";
import { CitationList, ErrorBox, PageHeader, Panel, TextArea, Button, Badge, riskTone } from "@/components/ui";
import { api } from "@/lib/api";
import type { SupportQueryResponse } from "@/lib/types";

export default function SupportPage() {
  const [query, setQuery] = useState("social anxiety CBT self-help public resource");
  const [result, setResult] = useState<SupportQueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(await api.querySupportResources(trimmed));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not query support resources");
    } finally {
      setLoading(false);
    }
  }

  const status = result
    ? result.blocked
      ? {
          label: "blocked",
          tone: "danger" as const,
          explanation: "Safety escalation took priority over ordinary resource retrieval."
        }
      : result.unknown
        ? {
            label: "unknown",
            tone: "warn" as const,
            explanation: "No sufficiently grounded public resource was found."
          }
        : {
            label: "grounded",
            tone: "good" as const,
            explanation: "Answer grounded in verified public resources."
          }
    : null;

  return (
    <>
      <PageHeader
        title="Support Resources"
        description="Query verified public support resources. Crisis-like input bypasses ordinary retrieval and returns a safety-first response."
      />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel title="Query">
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ask for a public support resource..."
            />
            <div className="flex items-center justify-between gap-3">
              <ErrorBox message={error} />
              <Button type="submit" disabled={loading}>
                {loading ? "Searching..." : "Search"}
              </Button>
            </div>
          </form>
        </Panel>

        <Panel title="Result">
          {result ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <Badge tone={riskTone(result.safety_result.risk_level)}>
                  risk: {result.safety_result.risk_level}
                </Badge>
                {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
              </div>
              {status ? (
                <p className="text-sm text-slate-600">{status.explanation}</p>
              ) : null}
              <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                {result.answer}
              </p>
              <CitationList citations={result.citations} />
            </div>
          ) : (
            <p className="text-sm text-slate-500">No query yet.</p>
          )}
        </Panel>
      </div>
    </>
  );
}
