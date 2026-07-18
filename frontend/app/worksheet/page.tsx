"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { showDiagnostics } from "@/lib/diagnostics";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import { SessionReviewPanel } from "@/components/session-review-panel";
import type { WorksheetCreateResponse, WorksheetRecord } from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  EmptyState,
  ErrorBox,
  LLMUsageBadge,
  PageHeader,
  Panel,
  TextArea,
  riskTone
} from "@/components/ui";

const fieldLabels: Array<[keyof NonNullable<WorksheetCreateResponse["worksheet"]>["fields"], string]> = [
  ["situation", "情境"],
  ["automatic_thought", "自动想法"],
  ["emotion", "情绪"],
  ["emotion_intensity", "情绪强度"],
  ["evidence_for", "支持证据"],
  ["evidence_against", "反对证据"],
  ["alternative_thought", "替代想法"],
  ["next_action", "下一步行动"]
];

const SHOW_DIAGNOSTICS = showDiagnostics();

export default function WorksheetPage() {
  const auth = useRequireAuth();
  const [message, setMessage] = useState(
    "情境：明天课堂发言。自动想法：我肯定会说错被大家笑。情绪：焦虑。强度：7/10。支持证据：之前发言卡过壳。反对证据：上次小组讨论同学认真听我说完。替代想法：我可能会紧张，但可以先说核心观点。下一步：今晚练习开场两遍。"
  );
  const [result, setResult] = useState<WorksheetCreateResponse | null>(null);
  const [loadedWorksheet, setLoadedWorksheet] = useState<WorksheetRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [supplement, setSupplement] = useState("");

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    const worksheetId = new URLSearchParams(window.location.search).get("worksheet_id");
    if (!worksheetId) {
      return;
    }
    void loadWorksheet(worksheetId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, auth.userId]);

  async function loadWorksheet(worksheetId: string) {
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      const record = await api.getWorksheet(worksheetId);
      setLoadedWorksheet(record);
      setResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法载入 worksheet");
      setRetryAction({
        label: "重试载入",
        run: () => {
          void loadWorksheet(worksheetId);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!message.trim()) {
      setError("请先描述一个社交压力场景。");
      return;
    }
    await createWorksheet(message.trim());
  }

  async function createWorksheet(sourceMessage: string) {
    setLoading(true);
    setError(null);
    setRetryAction(null);
    try {
      setLoadedWorksheet(null);
      setResult(await api.createWorksheet(currentUserId(), sourceMessage));
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法创建 worksheet");
      setRetryAction({
        label: "重试生成",
        run: () => {
          void createWorksheet(sourceMessage);
        }
      });
    } finally {
      setLoading(false);
    }
  }

  async function supplementCurrentWorksheet() {
    const worksheet = result?.worksheet ?? loadedWorksheet;
    if (!worksheet || !supplement.trim()) {
      setError("请先输入要补充或更正的内容。");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await api.supplementWorksheet(
        worksheet.worksheet_id,
        currentUserId(),
        supplement.trim()
      );
      setLoadedWorksheet(null);
      setResult(next);
      setSupplement("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法补充反思表");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthGuard>
      <PageHeader
        title="结构化反思"
        description="把社交压力场景整理成 CBT 风格的自助反思表。它不是治疗，只帮助你更清楚地看见想法、情绪和下一步。"
      />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel title="输入场景">
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="描述一个社交压力场景..."
              className="min-h-64"
            />
            <Button type="submit" disabled={loading}>
              {loading ? "生成中..." : "生成反思表"}
            </Button>
          </form>
          <div className="mt-3">
            <ErrorBox
              message={error}
              onRetry={retryAction?.run}
              retrying={loading}
              retryLabel={retryAction?.label}
            />
          </div>
        </Panel>

        <div className="space-y-4">
          {loadedWorksheet ? (
            <>
              <Panel title="已载入的反思表">
                <p className="mb-3 rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
                  {loadedWorksheet.disclaimer}
                </p>
                <div className="grid gap-3 md:grid-cols-2">
                  {fieldLabels.map(([key, label]) => (
                    <div key={key} className="rounded-md border border-line p-3">
                      <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                        {label}
                      </div>
                      <div className="text-sm leading-6 text-slate-800">
                        {String(loadedWorksheet.fields[key] ?? "未填写")}
                      </div>
                    </div>
                  ))}
                </div>
              </Panel>
              <Panel title="来源">
                <CitationList citations={loadedWorksheet.citations} />
              </Panel>
              <SessionReviewPanel
                title="反思练习复盘"
                source="worksheet"
                sourceId={loadedWorksheet.worksheet_id}
              />
            </>
          ) : result ? (
            <>
              <Panel
                title={SHOW_DIAGNOSTICS ? "安全状态" : "反思状态"}
                action={
                  <Badge
                    tone={
                      SHOW_DIAGNOSTICS
                        ? riskTone(result.safety_result.risk_level)
                        : result.blocked
                          ? "danger"
                          : "good"
                    }
                  >
                    {SHOW_DIAGNOSTICS
                      ? result.safety_result.risk_level
                      : result.blocked
                        ? "已暂停普通练习"
                        : "已生成反思表"}
                  </Badge>
                }
              >
                {SHOW_DIAGNOSTICS ? (
                  <div className="mb-3 flex flex-wrap gap-2">
                    <LLMUsageBadge usage={result.safety_result.llm_usage} />
                    <LLMUsageBadge usage={result.llm_usage} />
                  </div>
                ) : null}
                <p className="text-sm leading-6 text-slate-700">{result.response}</p>
                <p className="mt-3 rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
                  {result.disclaimer}
                </p>
              </Panel>

              {result.worksheet && (
                <Panel title="反思字段">
                  <div className="grid gap-3 md:grid-cols-2">
                    {fieldLabels.map(([key, label]) => (
                      <div key={key} className="rounded-md border border-line p-3">
                        <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                          {label}
                        </div>
                        <div className="text-sm leading-6 text-slate-800">
                          {String(result.worksheet?.fields[key] ?? "未填写")}
                        </div>
                      </div>
                    ))}
                  </div>
                </Panel>
              )}

              {(result.missing_fields.length > 0 ||
                result.gentle_followup_questions.length > 0) && (
                <Panel title="可补充信息">
                  <div className="mb-3 flex flex-wrap gap-2">
                    {result.missing_fields.map((field) => (
                      <Badge key={field} tone="warn">
                        缺少：{field}
                      </Badge>
                    ))}
                  </div>
                  <ul className="space-y-1 text-sm text-slate-700">
                    {result.gentle_followup_questions.map((question) => (
                      <li key={question}>- {question}</li>
                    ))}
                  </ul>
                </Panel>
              )}

              {result.worksheet && (
                <Panel title="来源">
                  <CitationList citations={result.worksheet.citations} />
                </Panel>
              )}
              {result.worksheet && (
                <SessionReviewPanel
                  title="反思练习复盘"
                  source="worksheet"
                  sourceId={result.worksheet.worksheet_id}
                />
              )}
            </>
          ) : (
            <Panel title="结果">
              <EmptyState
                title="还没有反思表"
                description="生成后会看到提取出的字段、缺失信息、温和追问和来源。"
              />
            </Panel>
          )}
          {(result?.worksheet ?? loadedWorksheet) ? (
            <Panel title="继续补充或更正">
              <div className="space-y-3">
                <TextArea
                  value={supplement}
                  onChange={(event) => setSupplement(event.target.value)}
                  placeholder="回答上面的追问，或明确说明要更正哪个字段..."
                />
                <Button
                  type="button"
                  disabled={loading}
                  onClick={() => void supplementCurrentWorksheet()}
                >
                  {loading ? "更新中..." : "更新这份反思表"}
                </Button>
              </div>
            </Panel>
          ) : null}
        </div>
      </div>
    </AuthGuard>
  );
}

type RetryAction = {
  label: string;
  run: () => void;
};
