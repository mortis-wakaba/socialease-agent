"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_USER_ID } from "@/lib/constants";
import type { WorksheetCreateResponse } from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea,
  riskTone
} from "@/components/ui";

const fieldLabels: Array<[keyof NonNullable<WorksheetCreateResponse["worksheet"]>["fields"], string]> = [
  ["situation", "Situation"],
  ["automatic_thought", "Automatic thought"],
  ["emotion", "Emotion"],
  ["emotion_intensity", "Emotion intensity"],
  ["evidence_for", "Evidence for"],
  ["evidence_against", "Evidence against"],
  ["alternative_thought", "Alternative thought"],
  ["next_action", "Next action"]
];

export default function WorksheetPage() {
  const [message, setMessage] = useState(
    "情境：明天课堂发言。自动想法：我肯定会说错被大家笑。情绪：焦虑。强度：7/10。支持证据：之前发言卡过壳。反对证据：上次小组讨论同学认真听我说完。替代想法：我可能会紧张，但可以先说核心观点。下一步：今晚练习开场两遍。"
  );
  const [result, setResult] = useState<WorksheetCreateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!message.trim()) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setResult(await api.createWorksheet(DEMO_USER_ID, message.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create worksheet");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Worksheet"
        description="Create a CBT-style self-reflection worksheet with citations from the Social Skills knowledge base."
      />
      <div className="grid gap-4 lg:grid-cols-[420px_1fr]">
        <Panel title="Source">
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="描述一个社交压力场景..."
              className="min-h-64"
            />
            <Button type="submit" disabled={loading}>
              {loading ? "Creating..." : "Create Worksheet"}
            </Button>
          </form>
          <div className="mt-3">
            <ErrorBox message={error} />
          </div>
        </Panel>

        <div className="space-y-4">
          {result ? (
            <>
              <Panel
                title="Safety"
                action={
                  <Badge tone={riskTone(result.safety_result.risk_level)}>
                    {result.safety_result.risk_level}
                  </Badge>
                }
              >
                <p className="text-sm leading-6 text-slate-700">{result.response}</p>
                <p className="mt-3 rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
                  {result.disclaimer}
                </p>
              </Panel>

              {result.worksheet && (
                <Panel title="Fields">
                  <div className="grid gap-3 md:grid-cols-2">
                    {fieldLabels.map(([key, label]) => (
                      <div key={key} className="rounded-md border border-line p-3">
                        <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                          {label}
                        </div>
                        <div className="text-sm leading-6 text-slate-800">
                          {String(result.worksheet?.fields[key] ?? "Not filled")}
                        </div>
                      </div>
                    ))}
                  </div>
                </Panel>
              )}

              {(result.missing_fields.length > 0 ||
                result.gentle_followup_questions.length > 0) && (
                <Panel title="Follow-up">
                  <div className="mb-3 flex flex-wrap gap-2">
                    {result.missing_fields.map((field) => (
                      <Badge key={field} tone="warn">
                        missing: {field}
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
                <Panel title="Citations">
                  <CitationList citations={result.worksheet.citations} />
                </Panel>
              )}
            </>
          ) : (
            <Panel title="Result">
              <p className="text-sm text-slate-500">Create a worksheet to see structured fields.</p>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

