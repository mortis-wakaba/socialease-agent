"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { currentUserId } from "@/lib/auth";
import { Badge, Button, Panel } from "@/components/ui";
import type { InterventionPlanView } from "@/lib/types";

export function PausePracticePanel({
  compact = false,
  interventionPlanId = null,
  initialPaused = false,
  onPaused,
  onPersistPause,
  persistedMessage = "已保存暂停状态。"
}: {
  compact?: boolean;
  interventionPlanId?: string | null;
  initialPaused?: boolean;
  onPaused?: (plan: InterventionPlanView) => void;
  onPersistPause?: () => Promise<void>;
  persistedMessage?: string;
}) {
  const [locallyPaused, setLocallyPaused] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const paused = initialPaused || locallyPaused;

  async function pausePractice() {
    setStatus(null);
    if (!interventionPlanId) {
      if (onPersistPause) {
        setSaving(true);
        try {
          await onPersistPause();
          setLocallyPaused(true);
          setStatus(persistedMessage);
        } catch (err) {
          setLocallyPaused(false);
          setStatus(err instanceof Error ? err.message : "无法保存暂停状态");
        } finally {
          setSaving(false);
        }
        return;
      }
      setLocallyPaused(true);
      setStatus("已在本页暂停；这不会写入账号里的练习计划状态。");
      return;
    }
    setSaving(true);
    try {
      const result = await api.pauseInterventionPlan(interventionPlanId, currentUserId());
      setLocallyPaused(true);
      setStatus(persistedMessage);
      onPaused?.(result.plan);
    } catch (err) {
      setLocallyPaused(false);
      setStatus(err instanceof Error ? err.message : "无法保存暂停状态");
    } finally {
      setSaving(false);
    }
  }

  if (compact && !paused) {
    return (
      <Button type="button" variant="secondary" onClick={pausePractice}>
        暂停练习
      </Button>
    );
  }

  return (
    <Panel title="暂停与退出">
      <div className="space-y-3 text-sm leading-6 text-slate-700">
        <div className="flex flex-wrap gap-2">
          <Badge tone={paused ? "warn" : "neutral"}>
            {paused ? "已暂停" : "可随时暂停"}
          </Badge>
          <Badge tone="warn">非紧急服务</Badge>
        </div>
        <p>
          如果练习让你明显不适，可以先停下来。你不需要为了完成练习而继续推进。
          如果出现安全担忧，请优先联系可信任的人、学校心理中心或当地紧急服务。
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={pausePractice} disabled={saving}>
            {saving ? "保存中..." : "暂停练习"}
          </Button>
          <Link
            href="/chat"
            className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
          >
            查找支持资源
          </Link>
          <Link
            href="/history"
            className="rounded-md border border-line px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
          >
            回到历史
          </Link>
        </div>
        {status ? (
          <p className="rounded-md border border-line bg-panel p-3 text-sm leading-6 text-slate-700">
            {status}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
