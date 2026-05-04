"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_USER_ID, roleplayScenarios } from "@/lib/constants";
import type {
  RoleplayFeedback,
  RoleplayScenario,
  RoleplaySession
} from "@/lib/types";
import {
  Badge,
  Button,
  CitationList,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea,
} from "@/components/ui";

export default function PracticePage() {
  const [selectedScenario, setSelectedScenario] =
    useState<RoleplayScenario>("classroom_speech");
  const [difficulty, setDifficulty] = useState(2);
  const [session, setSession] = useState<RoleplaySession | null>(null);
  const [message, setMessage] = useState("我想先说我的核心观点。");
  const [feedback, setFeedback] = useState<RoleplayFeedback | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function startSession() {
    setLoading(true);
    setError(null);
    setFeedback(null);
    try {
      const result = await api.startRoleplay(
        DEMO_USER_ID,
        selectedScenario,
        difficulty
      );
      setSession(result.session);
      setStatus(result.session.retrieved_guidance.no_guidance_found ? "fallback" : "grounded");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start session");
    } finally {
      setLoading(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !message.trim()) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.sendRoleplayMessage(
        session.session_id,
        DEMO_USER_ID,
        message.trim()
      );
      setSession(result.session);
      setStatus(result.blocked ? `blocked: ${result.safety_result.risk_level}` : null);
      setMessage("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send message");
    } finally {
      setLoading(false);
    }
  }

  async function loadFeedback() {
    if (!session) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.getRoleplayFeedback(session.session_id, DEMO_USER_ID);
      setFeedback(result.feedback);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load feedback");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Practice"
        description="Start a RAG-grounded role-play session, send practice turns, and request structured feedback."
      />
      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Panel title="Scenarios">
            <div className="grid gap-2">
              {roleplayScenarios.map((scenario) => (
                <button
                  key={scenario.id}
                  onClick={() => setSelectedScenario(scenario.id)}
                  className={`rounded-lg border p-3 text-left hover:border-brand ${
                    selectedScenario === scenario.id
                      ? "border-brand bg-emerald-50"
                      : "border-line bg-white"
                  }`}
                >
                  <div className="font-medium text-ink">{scenario.title}</div>
                  <p className="mt-1 text-sm leading-5 text-slate-600">
                    {scenario.description}
                  </p>
                </button>
              ))}
            </div>
            <div className="mt-4 flex items-center gap-3">
              <label className="text-sm text-slate-600">Difficulty</label>
              <input
                type="range"
                min={1}
                max={5}
                value={difficulty}
                onChange={(event) => setDifficulty(Number(event.target.value))}
                className="w-full"
              />
              <Badge>{difficulty}/5</Badge>
            </div>
            <div className="mt-4">
              <Button onClick={startSession} disabled={loading}>
                Start Session
              </Button>
            </div>
          </Panel>
          <ErrorBox message={error} />
        </div>

        <div className="space-y-4">
          <Panel
            title="Role-play"
            action={status ? <Badge tone={status.startsWith("blocked") ? "danger" : "info"}>{status}</Badge> : null}
          >
            {session ? (
              <div className="space-y-3">
                <div className="rounded-md border border-line bg-panel p-3">
                  <div className="mb-2 flex flex-wrap gap-2">
                    <Badge tone="info">{session.scenario}</Badge>
                    <Badge>{session.session_id.slice(0, 8)}</Badge>
                    <Badge tone={session.retrieved_guidance.no_guidance_found ? "warn" : "good"}>
                      {session.retrieved_guidance.no_guidance_found
                        ? "no guidance found"
                        : "RAG grounded"}
                    </Badge>
                  </div>
                  <CitationList citations={session.retrieved_guidance.citations} />
                </div>
                <div className="max-h-[360px] space-y-3 overflow-y-auto rounded-md border border-line bg-panel p-3">
                  {session.messages.map((item, index) => (
                    <div key={`${item.created_at}-${index}`} className="rounded-md border border-line bg-white p-3">
                      <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                        {item.role}
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                        {item.content}
                      </p>
                    </div>
                  ))}
                </div>
                <form onSubmit={sendMessage} className="space-y-3">
                  <TextArea
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    placeholder="输入你的练习回复..."
                  />
                  <div className="flex gap-2">
                    <Button type="submit" disabled={loading}>
                      Send Turn
                    </Button>
                    <Button variant="secondary" onClick={loadFeedback} disabled={loading}>
                      Get Feedback
                    </Button>
                  </div>
                </form>
              </div>
            ) : (
              <p className="text-sm text-slate-500">Choose a scenario and start a session.</p>
            )}
          </Panel>

          {feedback && (
            <Panel title="Feedback">
              <div className="mb-4 grid gap-2 sm:grid-cols-4">
                <Badge tone="info">clarity {feedback.clarity_score}/5</Badge>
                <Badge tone="info">natural {feedback.naturalness_score}/5</Badge>
                <Badge tone="info">assertive {feedback.assertiveness_score}/5</Badge>
                <Badge tone="info">empathy {feedback.empathy_score}/5</Badge>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">Strengths</h3>
                  <ul className="space-y-1 text-sm text-slate-700">
                    {feedback.strengths.map((item) => (
                      <li key={item}>- {item}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">Suggestions</h3>
                  <ul className="space-y-1 text-sm text-slate-700">
                    {feedback.suggestions.map((item) => (
                      <li key={item}>- {item}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <div className="mt-4 rounded-md border border-line bg-panel p-3 text-sm text-slate-700">
                {feedback.next_try_prompt}
              </div>
              <div className="mt-4">
                <CitationList citations={feedback.citations} />
              </div>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
