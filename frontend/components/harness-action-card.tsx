"use client";

import Link from "next/link";
import type React from "react";
import { showDiagnostics, showTraceLinks } from "@/lib/diagnostics";
import type { ChatResponse, HarnessAction } from "@/lib/types";
import { useDeveloperAccess } from "@/lib/use-developer-access";
import { Badge, Button } from "@/components/ui";

const SHOW_DIAGNOSTICS = showDiagnostics();
const SHOW_TRACE_LINKS = showTraceLinks();

type HarnessActionCardProps = {
  result: ChatResponse;
  approving?: boolean;
  onApproveConsent?: (protocolId: string) => void;
  onRejectConsent?: (protocolId: string) => void;
};

export function HarnessActionCard({
  result,
  approving = false,
  onApproveConsent,
  onRejectConsent
}: HarnessActionCardProps) {
  const action = readString(result.structured_data.action) as HarnessAction | null;
  if (!action) {
    return null;
  }

  if (action === "crisis_escalation") {
    return (
      <div className="mt-3 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm">
        <div className="mb-2 flex flex-wrap gap-2">
          <Badge tone="danger">危机升级</Badge>
          <Badge tone="neutral">普通练习已暂停</Badge>
        </div>
        <p className="leading-6 text-rose-800">
          普通练习入口已隐藏。请优先联系现实支持、学校心理中心或当地紧急服务。
        </p>
        <div className="mt-3">
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </div>
    );
  }

  if (action === "consent_required") {
    const protocolId = readString(result.structured_data.protocol_id);
    return (
      <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
        <div className="mb-2 flex flex-wrap gap-2">
          <Badge tone="warn">需要同意</Badge>
          <Badge tone="neutral">
            {SHOW_DIAGNOSTICS
              ? readString(result.structured_data.harness_action) ?? "练习"
              : consentActionLabel(readString(result.structured_data.harness_action))}
          </Badge>
        </div>
        <p className="leading-6 text-amber-900">
          这个练习动作需要你明确同意后才会继续。你可以同意，也可以取消。
        </p>
        {protocolId ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              onClick={() => onApproveConsent?.(protocolId)}
              disabled={approving}
            >
              {approving ? "处理中..." : "同意并继续"}
            </Button>
            <Button
              variant="secondary"
              onClick={() => onRejectConsent?.(protocolId)}
              disabled={approving}
            >
              取消
            </Button>
            <TraceLinkButton runId={result.run_id}>
              查看 Trace
            </TraceLinkButton>
          </div>
        ) : null}
      </div>
    );
  }

  if (action === "roleplay_started") {
    const sessionId = readString(result.structured_data.session_id);
    return (
      <ActionPanel tone="good" label="角色扮演已开始">
        <p className="leading-6 text-emerald-900">
          已创建角色扮演会话。可以进入练习页面继续并获取反馈。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <LinkButton href={sessionId ? `/practice?session_id=${encodeURIComponent(sessionId)}` : "/practice"}>
            打开练习
          </LinkButton>
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </ActionPanel>
    );
  }

  if (action === "worksheet_created") {
    const worksheetId = readString(result.structured_data.worksheet_id);
    return (
      <ActionPanel tone="info" label="反思表已生成">
        <p className="leading-6 text-sky-900">
          已生成自助反思表。可以打开反思页面查看结构化字段和来源。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <LinkButton href={worksheetId ? `/worksheet?worksheet_id=${encodeURIComponent(worksheetId)}` : "/worksheet"}>
            打开反思
          </LinkButton>
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </ActionPanel>
    );
  }

  if (action === "exposure_plan_created") {
    const planId = readString(result.structured_data.plan_id);
    const previewTasks = Array.isArray(result.structured_data.preview_tasks)
      ? result.structured_data.preview_tasks.slice(0, 2)
      : [];
    return (
      <ActionPanel tone="good" label="练习阶梯已创建">
        <p className="leading-6 text-emerald-900">
          已生成社交练习阶梯。建议先看低强度步骤，再进入 Progress 页面继续调整。
        </p>
        {previewTasks.length > 0 ? (
          <div className="mt-3 space-y-2">
            {previewTasks.map((task, index) => (
              <div key={index} className="rounded-md border border-emerald-200 bg-white p-2">
                <div className="text-sm font-medium text-ink">
                  {readStringFromObject(task, "title") ?? `任务 ${index + 1}`}
                </div>
                <div className="mt-1 text-xs leading-5 text-slate-600">
                  难度 {readNumberFromObject(task, "difficulty") ?? "?"}/10
                </div>
              </div>
            ))}
          </div>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <LinkButton href={planId ? `/progress?plan_id=${encodeURIComponent(planId)}` : "/progress"}>
            查看计划
          </LinkButton>
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </ActionPanel>
    );
  }

  if (action === "support_resources_queried") {
    return (
      <ActionPanel tone="info" label="资源查询完成">
        <p className="leading-6 text-sky-900">
          已完成资源查询。需要继续查找公开支持资源时，可以进入资源页面。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <LinkButton href="/support">打开资源</LinkButton>
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </ActionPanel>
    );
  }

  if (action === "action_blocked" || action === "skill_failed") {
    return (
      <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
        <div className="mb-2 flex flex-wrap gap-2">
          <Badge tone={action === "skill_failed" ? "warn" : "danger"}>
            {action === "skill_failed" ? "暂时无法完成" : "已暂停"}
          </Badge>
          {readString(result.structured_data.error_category) ? (
            SHOW_DIAGNOSTICS ? (
              <Badge tone="warn">{readString(result.structured_data.error_category)}</Badge>
            ) : null
          ) : null}
        </div>
        <p className="leading-6 text-slate-700">
          系统暂停了这一步。建议先选择更低强度的练习，或联系可信任的人获得现实支持。
        </p>
        <div className="mt-3">
          <TraceLinkButton runId={result.run_id}>
            查看 Trace
          </TraceLinkButton>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-md border border-line bg-panel p-3 text-sm">
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge tone="neutral">
          {SHOW_DIAGNOSTICS ? `动作: ${action}` : "已完成处理"}
        </Badge>
      </div>
      <TraceLinkButton runId={result.run_id}>
        查看 Trace
      </TraceLinkButton>
    </div>
  );
}

function ActionPanel({
  tone,
  label,
  children
}: {
  tone: "good" | "info";
  label: string;
  children: React.ReactNode;
}) {
  const styles =
    tone === "good"
      ? "border-emerald-200 bg-emerald-50"
      : "border-sky-200 bg-sky-50";
  return (
    <div className={`mt-3 rounded-md border p-3 text-sm ${styles}`}>
      <div className="mb-2">
        <Badge tone={tone}>{label}</Badge>
      </div>
      {children}
    </div>
  );
}

function LinkButton({
  href,
  children
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="inline-flex rounded-md border border-line bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:border-brand hover:text-brand"
    >
      {children}
    </Link>
  );
}

function TraceLinkButton({
  runId,
  children
}: {
  runId: string;
  children: React.ReactNode;
}) {
  const developerAccess = useDeveloperAccess();
  const allowed = SHOW_TRACE_LINKS && developerAccess.allowed;

  if (!allowed) {
    return null;
  }
  return (
    <LinkButton href={`/trace?run_id=${encodeURIComponent(runId)}`}>
      {children}
    </LinkButton>
  );
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function readStringFromObject(value: unknown, key: string): string | null {
  if (!value || typeof value !== "object" || !(key in value)) {
    return null;
  }
  return readString((value as Record<string, unknown>)[key]);
}

function readNumberFromObject(value: unknown, key: string): number | null {
  if (!value || typeof value !== "object" || !(key in value)) {
    return null;
  }
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "number" ? field : null;
}

function consentActionLabel(action: string | null): string {
  const labels: Record<string, string> = {
    start_roleplay: "角色扮演练习",
    create_worksheet: "结构化反思",
    create_exposure_plan: "社交练习计划",
    complete_exposure_task: "练习反馈",
    query_support_resource: "支持资源查询",
    write_memory: "保存练习偏好"
  };
  return action ? labels[action] ?? "练习动作" : "练习动作";
}
