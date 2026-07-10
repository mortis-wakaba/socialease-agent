"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import type { SessionReviewCompletion, SessionReviewSource } from "@/lib/types";
import { Badge, Button, Panel, TextArea } from "@/components/ui";

export function SessionReviewPanel({
  title = "30 秒复盘",
  defaultBefore = 6,
  defaultAfter = 4,
  source = "general",
  sourceId = null
}: {
  title?: string;
  defaultBefore?: number;
  defaultAfter?: number;
  source?: SessionReviewSource;
  sourceId?: string | null;
}) {
  const [completed, setCompleted] = useState<SessionReviewCompletion>("completed");
  const [before, setBefore] = useState(defaultBefore);
  const [after, setAfter] = useState(defaultAfter);
  const [nextStep, setNextStep] = useState("继续下一次低强度练习");
  const [saveRecord, setSaveRecord] = useState(true);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function saveReview() {
    if (!nextStep.trim()) {
      setStatus("请先写一句下一步。");
      return;
    }
    setSaving(true);
    setStatus(null);
    try {
      const result = await api.createSessionReview(currentUserId(), {
        source,
        sourceId,
        completed,
        anxietyBefore: before,
        anxietyAfter: after,
        nextStep: nextStep.trim(),
        saveRecord
      });
      setStatus(result.message);
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "无法保存复盘");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Panel title={title}>
      <div className="space-y-3 text-sm text-slate-700">
        <div className="flex flex-wrap gap-2">
          <ReviewChoice
            active={completed === "completed"}
            label="完成了"
            onClick={() => setCompleted("completed")}
          />
          <ReviewChoice
            active={completed === "partial"}
            label="完成一部分"
            onClick={() => setCompleted("partial")}
          />
          <ReviewChoice
            active={completed === "pause"}
            label="先暂停"
            onClick={() => setCompleted("pause")}
          />
        </div>
        <label className="block">
          练习前焦虑
          <div className="mt-2 flex items-center gap-3">
            <input
              type="range"
              min={1}
              max={10}
              value={before}
              onChange={(event) => setBefore(Number(event.target.value))}
              className="w-full"
            />
            <Badge>{before}/10</Badge>
          </div>
        </label>
        <label className="block">
          练习后焦虑
          <div className="mt-2 flex items-center gap-3">
            <input
              type="range"
              min={1}
              max={10}
              value={after}
              onChange={(event) => setAfter(Number(event.target.value))}
              className="w-full"
            />
            <Badge>{after}/10</Badge>
          </div>
        </label>
        <label className="block">
          下一步
          <TextArea
            value={nextStep}
            onChange={(event) => setNextStep(event.target.value)}
            className="mt-1 min-h-20"
          />
        </label>
        <label className="flex items-center gap-2 text-sm leading-6 text-slate-700">
          <input
            type="checkbox"
            checked={saveRecord}
            onChange={(event) => setSaveRecord(event.target.checked)}
          />
          保存为低敏结构化练习记录。
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="secondary" onClick={saveReview} disabled={saving}>
            {saving ? "保存中..." : "保存复盘"}
          </Button>
          <Link
            href="/history"
            className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
          >
            查看历史
          </Link>
        </div>
        {status ? (
          <p className="rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
            {status}
          </p>
        ) : null}
        <p className="text-xs leading-5 text-slate-500">
          复盘只保存完成状态、焦虑强度和下一步摘要。不要写入可识别个人身份的信息。
        </p>
      </div>
    </Panel>
  );
}

function ReviewChoice({
  active,
  label,
  onClick
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border px-3 py-2 text-sm ${
        active ? "border-brand bg-emerald-50 text-brand" : "border-line bg-white"
      }`}
    >
      {label}
    </button>
  );
}
