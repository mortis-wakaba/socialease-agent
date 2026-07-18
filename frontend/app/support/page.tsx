"use client";

import { FormEvent, useState } from "react";
import { CitationList, EmptyState, ErrorBox, PageHeader, Panel, TextArea, Button, Badge, LLMUsageBadge, riskTone } from "@/components/ui";
import { api } from "@/lib/api";
import { showDiagnostics } from "@/lib/diagnostics";
import type { SupportQueryResponse } from "@/lib/types";

const SHOW_DIAGNOSTICS = showDiagnostics();

export default function SupportPage() {
  const [query, setQuery] = useState("社交焦虑 自助 公开资源");
  const [result, setResult] = useState<SupportQueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryQuery, setRetryQuery] = useState<string | null>(null);
  const [searchSessionId, setSearchSessionId] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setError("请输入要查询的支持资源。");
      return;
    }
    await runQuery(trimmed);
  }

  async function runQuery(trimmed: string) {
    setLoading(true);
    setError(null);
    setRetryQuery(null);
    try {
      const next = await api.querySupportResources(trimmed, searchSessionId);
      setResult(next);
      setSearchSessionId(next.search_session_id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法查询支持资源");
      setRetryQuery(trimmed);
    } finally {
      setLoading(false);
    }
  }

  const status = result
    ? result.blocked
      ? {
          label: "已暂停",
          tone: "danger" as const,
          explanation: "检测到安全风险，系统优先返回安全回应，而不是继续普通资源检索。"
        }
      : result.unknown
        ? {
            label: "未找到",
            tone: "warn" as const,
            explanation: "知识库中没有找到足够可靠的公开资源。"
          }
        : {
            label: "有来源",
            tone: "good" as const,
            explanation: "回答基于已收录的公开资源。"
          }
    : null;

  return (
    <>
      <PageHeader
        title="支持资源"
        description="查询已收录的公开支持资源。危机表达会绕过普通检索，优先返回安全回应。"
      />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel title="查询">
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入你想了解的支持资源..."
            />
            <div className="flex items-center justify-between gap-3">
              <ErrorBox
                message={error}
                onRetry={retryQuery ? () => void runQuery(retryQuery) : undefined}
                retrying={loading}
              />
              <Button type="submit" disabled={loading}>
                {loading ? "查询中..." : "查询"}
              </Button>
            </div>
          </form>
        </Panel>

        <Panel title="结果">
          {result ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                {SHOW_DIAGNOSTICS ? (
                  <>
                    <Badge tone={riskTone(result.safety_result.risk_level)}>
                      风险: {result.safety_result.risk_level}
                    </Badge>
                    <LLMUsageBadge usage={result.safety_result.llm_usage} />
                  </>
                ) : (
                  <Badge tone={result.blocked ? "danger" : "good"}>
                    {result.blocked ? "已暂停普通检索" : "已完成安全检查"}
                  </Badge>
                )}
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
            <EmptyState
              title="还没有查询"
              description="输入一个公开支持资源问题。这里不会编造真实学校电话或不存在的资源。"
            />
          )}
        </Panel>
      </div>
    </>
  );
}
