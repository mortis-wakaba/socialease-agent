"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import { DEMO_USER_ID } from "@/lib/constants";
import type { ChatResponse } from "@/lib/types";
import {
  Badge,
  Button,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea,
  riskTone
} from "@/components/ui";

type ChatMessage = {
  role: "user" | "agent";
  content: string;
  result?: ChatResponse;
};

export default function ChatPage() {
  const [input, setInput] = useState("我想模拟课堂发言，怕自己说不清楚");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [latest, setLatest] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) {
      return;
    }
    setLoading(true);
    setError(null);
    setMessages((items) => [...items, { role: "user", content: trimmed }]);

    try {
      const result = await api.chat(DEMO_USER_ID, trimmed);
      setLatest(result);
      setMessages((items) => [
        ...items,
        { role: "agent", content: result.response, result }
      ]);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Chat"
        description="Run the core Safety → Router → Agent → Trace workflow from a simple chat surface."
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Panel title="Conversation">
          <div className="mb-4 min-h-[360px] space-y-3 rounded-md border border-line bg-panel p-3">
            {messages.length === 0 ? (
              <p className="text-sm text-slate-500">Send a message to start a run.</p>
            ) : (
              messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`rounded-lg border p-3 ${
                    message.role === "user"
                      ? "ml-auto max-w-[85%] border-brand bg-white"
                      : "mr-auto max-w-[90%] border-line bg-white"
                  }`}
                >
                  <div className="mb-1 text-xs font-medium uppercase text-slate-500">
                    {message.role}
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                    {message.content}
                  </p>
                </div>
              ))
            )}
          </div>
          <form onSubmit={handleSubmit} className="space-y-3">
            <TextArea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="输入一个社交压力场景..."
            />
            <div className="flex items-center justify-between gap-3">
              <ErrorBox message={error} />
              <Button type="submit" disabled={loading}>
                {loading ? "Sending..." : "Send"}
              </Button>
            </div>
          </form>
        </Panel>

        <Panel title="Run Status">
          {latest ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge tone={riskTone(latest.risk_level)}>
                  risk: {latest.risk_level}
                </Badge>
                <Badge tone="info">intent: {latest.intent}</Badge>
              </div>
              <div>
                <div className="text-xs font-medium uppercase text-slate-500">run_id</div>
                <p className="break-all rounded-md border border-line bg-panel p-2 text-xs text-slate-700">
                  {latest.run_id}
                </p>
              </div>
              <div className="rounded-md border border-line p-3 text-sm">
                <div className="font-medium text-ink">Selected agent</div>
                <div className="mt-1 text-slate-700">{latest.trace.selected_agent}</div>
              </div>
              <div className="rounded-md border border-line p-3 text-sm">
                <div className="font-medium text-ink">Latency</div>
                <div className="mt-1 text-slate-700">
                  {latest.trace.latency_ms.toFixed(2)} ms
                </div>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No run yet.</p>
          )}
        </Panel>
      </div>
    </>
  );
}

