"use client";

import type { ConsentRequiredDetail } from "@/lib/types";
import { Badge, Button } from "@/components/ui";

export function DirectConsentCard({
  detail,
  approving,
  onApprove,
  onReject
}: {
  detail: ConsentRequiredDetail;
  approving?: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
      <div className="mb-2 flex flex-wrap gap-2">
        <Badge tone="warn">需要同意</Badge>
        <Badge>{detail.harness_action}</Badge>
        <Badge>{detail.protocol_status}</Badge>
      </div>
      <p className="leading-6 text-amber-900">
        这个直接练习动作需要明确同意后才会执行。你可以同意继续，也可以取消；这只是社交练习，不是诊断或治疗。
      </p>
      {detail.protocol_expires_at ? (
        <p className="mt-2 text-xs text-amber-800">
          过期时间：{new Date(detail.protocol_expires_at).toLocaleString()}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={onApprove} disabled={approving}>
          {approving ? "处理中..." : "同意并继续"}
        </Button>
        <Button variant="secondary" onClick={onReject} disabled={approving}>
          取消
        </Button>
      </div>
    </div>
  );
}
