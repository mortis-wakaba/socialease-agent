"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AuthGuard } from "@/components/auth-guard";
import { Badge, EmptyState, ErrorBox, PageHeader, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import type { ExposurePlan } from "@/lib/types";
import { useRequireAuth } from "@/lib/use-require-auth";

export default function ProgressPage() {
  const auth = useRequireAuth();
  const [plan, setPlan] = useState<ExposurePlan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !auth.userId) {
      return;
    }
    const userId = auth.userId;
    const planId = new URLSearchParams(window.location.search).get("plan_id");
    const request = planId
      ? api.getExposurePlan(planId, userId)
      : api.getUserExposure(userId);
    void request
      .then((result) => {
        setPlan(result.plan);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法载入练习进度");
      });
  }, [auth.authenticated, auth.ready, auth.userId]);

  return (
    <AuthGuard>
      <PageHeader
        title="社交练习进度"
        description="这里仅展示已保存的练习阶梯；创建、反馈和继续练习都在统一对话中完成。"
      />
      <div className="space-y-4">
        {error ? <ErrorBox message={error} /> : null}
        <Panel title="当前计划">
          {!plan ? (
            <EmptyState
              title="还没有练习计划"
              description="在统一对话中描述目标场景，系统只会先提供模块选项。"
            />
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="info">当前强度 {plan.current_anxiety_level}/10</Badge>
                <Badge>{plan.attempts.length} 次反馈</Badge>
              </div>
              <p className="text-sm leading-6 text-slate-700">{plan.target_scenario}</p>
              <ol className="space-y-2">
                {plan.tasks.map((task, index) => (
                  <li key={task.task_id} className="rounded-md border border-line bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium text-ink">
                        {index + 1}. {task.title}
                      </span>
                      <Badge>{task.difficulty}/10</Badge>
                    </div>
                    <p className="mt-1 text-sm leading-6 text-slate-600">{task.description}</p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </Panel>
        <Panel title="继续">
          <p className="text-sm leading-6 text-slate-700">
            练习反馈不会在这里单独写入。回到同一 Conversation 后，可以报告完成情况、
            前后压力强度，也可以随时暂停或结束模块。
          </p>
          <div className="mt-3">
            <Link
              href="/chat"
              className="inline-flex rounded-md bg-brand px-4 py-2 text-sm font-medium text-white"
            >
              返回统一对话
            </Link>
          </div>
        </Panel>
      </div>
    </AuthGuard>
  );
}
