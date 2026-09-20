"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useRequireAuth } from "@/lib/use-require-auth";
import { AuthGuard } from "@/components/auth-guard";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBox,
  PageHeader,
  Panel,
  TextArea
} from "@/components/ui";
import type {
  Conversation,
  ConversationStreamCompletedResult,
  ConversationEvent,
  ModuleProposal,
  ModuleRun,
  ModuleType
} from "@/lib/types";

const HISTORY_NOTICE_VERSION = "2026-07-01";
const HISTORY_NOTICE_KEY = `socialease:history-notice:${HISTORY_NOTICE_VERSION}`;
const COMMAND_RECEIPT_KEY = "socialease:conversation-command-recovery";

type PendingMessage = {
  conversationId: string;
  idempotencyKey: string;
  message: string;
};

export default function ChatPage() {
  const auth = useRequireAuth();
  const userId = auth.userId;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [events, setEvents] = useState<ConversationEvent[]>([]);
  const [moduleStack, setModuleStack] = useState<ModuleRun[]>([]);
  const [proposals, setProposals] = useState<ModuleProposal[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [streamedResponse, setStreamedResponse] = useState("");
  const [persistenceNotice, setPersistenceNotice] = useState<string | null>(null);
  const [pendingMessage, setPendingMessage] = useState<PendingMessage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showNotice, setShowNotice] = useState(false);
  const [noticeAcknowledged, setNoticeAcknowledged] = useState(false);

  useEffect(() => {
    if (!auth.ready || !auth.authenticated || !userId) {
      return;
    }
    void loadConversationList(userId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.ready, auth.authenticated, userId]);

  async function loadConversationList(effectiveUserId: string) {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listConversations(effectiveUserId);
      setConversations(result.items);
      if (result.items.length > 0) {
        const requestedId = new URLSearchParams(window.location.search).get(
          "conversation_id"
        );
        const selected =
          result.items.find((item) => item.conversation_id === requestedId) ??
          result.items[0];
        await selectConversation(selected);
      } else {
        setCurrent(null);
        setEvents([]);
      }
    } catch (err) {
      setError(errorMessage(err, "无法载入对话历史"));
    } finally {
      setLoading(false);
    }
  }

  async function selectConversation(conversation: Conversation) {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    setStreamedResponse("");
    setPersistenceNotice(null);
    setPendingMessage(null);
    try {
      const detail = await api.getConversation(
        conversation.conversation_id,
        userId
      );
      setCurrent(detail.conversation);
      setEvents(detail.events.items);
      setModuleStack(detail.active_module_stack);
      setProposals(detail.pending_module_proposals);
      setNextCursor(detail.events.next_cursor ?? null);
      await recoverPendingCommand(conversation, userId);
    } catch (err) {
      setError(errorMessage(err, "无法载入这段对话"));
    } finally {
      setLoading(false);
    }
  }

  function requestNewConversation() {
    const acknowledged =
      window.localStorage.getItem(HISTORY_NOTICE_KEY) === "acknowledged";
    if (!acknowledged) {
      setNoticeAcknowledged(false);
      setShowNotice(true);
      return;
    }
    void createConversation();
  }

  async function createConversation() {
    if (!userId) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const conversation = await api.createConversation(userId);
      window.localStorage.setItem(HISTORY_NOTICE_KEY, "acknowledged");
      setShowNotice(false);
      setConversations((items) => [conversation, ...items]);
      setCurrent(conversation);
      setEvents([]);
      setModuleStack([]);
      setProposals([]);
      setNextCursor(null);
    } catch (err) {
      setError(errorMessage(err, "无法新建对话"));
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!current || !userId || loading) {
      return;
    }
    const message = input.trim();
    if (!message) {
      setError("发送前请先输入内容。");
      return;
    }
    setLoading(true);
    setError(null);
    setInput("");
    setStreamedResponse("");
    setPersistenceNotice(null);
    let receipt = readCommandReceipt(current.conversation_id);
    let reuseUnfinishedSafetyCommand = false;
    let reuseConfirmedMissingCommand = false;
    const knownSameRequest =
      pendingMessage?.conversationId === current.conversation_id &&
      pendingMessage.idempotencyKey === receipt?.idempotencyKey &&
      pendingMessage.message === message;
    if (receipt && !knownSameRequest) {
      try {
        const recovered = await api.getConversationCommand(
          current.conversation_id,
          userId,
          receipt.idempotencyKey
        );
        if (recovered.status === "completed" && recovered.result) {
          applyCompletedResult(recovered.result);
          clearCommandReceipt(current.conversation_id);
          receipt = null;
        } else if (recovered.status === "abandoned") {
          clearCommandReceipt(current.conversation_id);
          receipt = null;
        } else if (recovered.status === "not_found") {
          reuseConfirmedMissingCommand = true;
        } else if (recovered.status === "recoverable" && recovered.can_abandon) {
          await api.abandonConversationCommand(
            current.conversation_id,
            userId,
            receipt.idempotencyKey
          );
          clearCommandReceipt(current.conversation_id);
          receipt = null;
        } else if (
          recovered.safety_recovery_status === "recovery_pending" ||
          recovered.safety_recovery_status === "classification_pending"
        ) {
          if (pendingMessage === null && recovered.can_retry_same_request) {
            reuseUnfinishedSafetyCommand = true;
          } else {
            setInput(message);
            setPersistenceNotice(
              "上一条危机请求的安全状态尚未完成；请重试原消息，完成前不会开始普通请求。"
            );
            setLoading(false);
            return;
          }
        } else {
          setInput(message);
          setPersistenceNotice("上一条请求状态尚未确定，暂未发送新消息。");
          setLoading(false);
          return;
        }
      } catch (err) {
        setInput(message);
        setError(errorMessage(err, "无法核对上一条消息的恢复状态"));
        setLoading(false);
        return;
      }
    }
    const reusable =
      receipt !== null &&
      (knownSameRequest ||
        reuseUnfinishedSafetyCommand ||
        reuseConfirmedMissingCommand);
    const idempotencyKey = reusable
      ? receipt!.idempotencyKey
      : createIdempotencyKey();
    const pending = {
      conversationId: current.conversation_id,
      idempotencyKey,
      message
    };
    setPendingMessage(pending);
    writeCommandReceipt({
      conversationId: current.conversation_id,
      idempotencyKey
    });
    try {
      const result = await api.sendConversationMessageStream(
        current.conversation_id,
        userId,
        message,
        idempotencyKey,
        (chunk) => setStreamedResponse((value) => value + chunk)
      );
      applyCompletedResult(result);
      if (result.crisis_persistence_status === "degraded_retry_required") {
        setStreamedResponse(result.transient_response ?? "");
        setInput(message);
        setPersistenceNotice(
          result.transient_response
            ? "这条安全指引尚未保存；请使用相同消息重试。"
            : "安全指引已显示，但相关状态尚未完整保存；请使用相同消息重试。"
        );
        writeCommandReceipt({
          conversationId: current.conversation_id,
          idempotencyKey,
          ...(result.transient_response
            ? { transientResponse: result.transient_response }
            : {})
        });
      } else if (
        result.crisis_persistence_status ===
        "safety_state_persisted_command_unconfirmed"
      ) {
        setStreamedResponse("");
        setInput(message);
        setPersistenceNotice("安全状态已保存，但请求完成状态待确认；可以重试。 ");
      } else {
        clearCommandReceipt(current.conversation_id);
        setPendingMessage(null);
        setStreamedResponse("");
      }
    } catch (err) {
      setInput(message);
      setError(errorMessage(err, "消息发送失败"));
    } finally {
      setLoading(false);
    }
  }

  function applyCompletedResult(result: ConversationStreamCompletedResult) {
    setEvents((items) => mergeEvents(items, result.appended_events));
    setCurrent(result.conversation);
    setModuleStack(result.active_module_stack);
    if (result.pending_module_proposal) {
      setProposals((items) =>
        mergeProposals(items, [result.pending_module_proposal!])
      );
    }
    updateConversationSummary(result.conversation);
  }

  async function recoverPendingCommand(
    conversation: Conversation,
    effectiveUserId: string
  ) {
    const receipt = readCommandReceipt(conversation.conversation_id);
    if (!receipt) {
      return;
    }
    if (receipt.transientResponse) {
      setStreamedResponse(receipt.transientResponse);
      setPersistenceNotice("这条安全指引尚未保存；正在核对请求状态。");
    }
    try {
      const recovered = await api.getConversationCommand(
        conversation.conversation_id,
        effectiveUserId,
        receipt.idempotencyKey
      );
      if (recovered.status === "completed" && recovered.result) {
        applyCompletedResult(recovered.result);
        setStreamedResponse(recovered.result.transient_response ?? "");
        setPersistenceNotice(null);
        clearCommandReceipt(conversation.conversation_id);
        setPendingMessage(null);
        return;
      }
      if (recovered.status === "recoverable") {
        setPersistenceNotice(
          recovered.safety_recovery_status === "recovery_pending"
            ? "上一条危机请求仍需恢复安全状态；请重试原消息，完成前不会开始普通请求。"
            : recovered.safety_recovery_status === "classification_pending"
              ? "上一条请求的安全状态尚未确认；请重试原消息，完成前不会开始普通请求。"
              : "上一条请求未完成；下次发送前会先安全终止旧命令。"
        );
        return;
      }
      if (recovered.status === "abandoned") {
        clearCommandReceipt(conversation.conversation_id);
        setPendingMessage(null);
        setPersistenceNotice("上一条命令已结束；如仍需要，请重新输入后发送。");
        return;
      }
      if (recovered.status === "not_found") {
        setPersistenceNotice(
          "上一条请求未到达服务端；请重新输入后发送，系统会沿用原恢复键安全重试。"
        );
        return;
      }
      setPersistenceNotice("上一条请求仍在后台处理，完成后刷新即可恢复结果。");
    } catch (err) {
      setError(errorMessage(err, "无法核对上一条消息的恢复状态"));
    }
  }

  async function loadMoreEvents() {
    if (!current || !userId || !nextCursor) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const detail = await api.getConversation(
        current.conversation_id,
        userId,
        nextCursor
      );
      setEvents((items) => mergeEvents(items, detail.events.items));
      setNextCursor(detail.events.next_cursor ?? null);
      setModuleStack(detail.active_module_stack);
      setProposals(detail.pending_module_proposals);
    } catch (err) {
      setError(errorMessage(err, "无法继续载入历史"));
    } finally {
      setLoading(false);
    }
  }

  async function acceptProposal(proposal: ModuleProposal) {
    if (!userId || !current) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.acceptModuleProposal(
        current.conversation_id,
        proposal.proposal_id,
        userId,
        proposal.request_hash
      );
      setEvents((items) => mergeEvents(items, result.appended_events));
      setModuleStack(result.active_module_stack);
      setCurrent(result.conversation);
      setProposals((items) =>
        items.filter((item) => item.proposal_id !== proposal.proposal_id)
      );
      updateConversationSummary(result.conversation);
    } catch (err) {
      setError(errorMessage(err, "无法进入模块"));
    } finally {
      setLoading(false);
    }
  }

  async function rejectProposal(proposal: ModuleProposal) {
    if (!userId || !current) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.rejectModuleProposal(
        current.conversation_id,
        proposal.proposal_id,
        userId,
        proposal.request_hash
      );
      setProposals((items) =>
        items.filter((item) => item.proposal_id !== proposal.proposal_id)
      );
    } catch (err) {
      setError(errorMessage(err, "无法取消模块选项"));
    } finally {
      setLoading(false);
    }
  }

  async function terminateCurrentModule() {
    if (!userId || !current || moduleStack.length === 0) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const top = moduleStack[moduleStack.length - 1];
      const result = await api.terminateModule(
        current.conversation_id,
        top.module_run_id,
        userId
      );
      setEvents((items) => mergeEvents(items, result.appended_events));
      setModuleStack(result.active_module_stack);
      setCurrent(result.conversation);
      updateConversationSummary(result.conversation);
    } catch (err) {
      setError(errorMessage(err, "无法结束当前模块"));
    } finally {
      setLoading(false);
    }
  }

  async function terminateAllModules() {
    if (!userId || !current || moduleStack.length === 0) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.terminateAllModules(
        current.conversation_id,
        userId
      );
      setEvents((items) => mergeEvents(items, result.appended_events));
      setModuleStack(result.active_module_stack);
      setCurrent(result.conversation);
      updateConversationSummary(result.conversation);
    } catch (err) {
      setError(errorMessage(err, "无法结束全部模块"));
    } finally {
      setLoading(false);
    }
  }

  async function renameConversation() {
    if (!userId || !current) {
      return;
    }
    const title = window.prompt("新的对话标题", current.title)?.trim();
    if (!title || title === current.title) {
      return;
    }
    await updateCurrentConversation({ title });
  }

  async function archiveConversation() {
    if (!current) {
      return;
    }
    await updateCurrentConversation({
      status: current.status === "archived" ? "active" : "archived"
    });
  }

  async function updateCurrentConversation(update: {
    title?: string;
    status?: "active" | "archived";
  }) {
    if (!userId || !current) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const updated = await api.updateConversation(
        current.conversation_id,
        userId,
        current.version,
        update
      );
      setCurrent(updated);
      updateConversationSummary(updated);
    } catch (err) {
      setError(errorMessage(err, "无法更新对话"));
    } finally {
      setLoading(false);
    }
  }

  async function exportConversation() {
    if (!userId || !current) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const exported = await api.exportConversation(
        current.conversation_id,
        userId
      );
      downloadJson(`socialease-${current.conversation_id}.json`, exported);
    } catch (err) {
      setError(errorMessage(err, "无法导出对话"));
    } finally {
      setLoading(false);
    }
  }

  async function deleteConversation() {
    if (!userId || !current) {
      return;
    }
    if (!window.confirm("永久删除这段对话及其模块状态？此操作无法撤销。")) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.deleteConversation(current.conversation_id, userId);
      const remaining = conversations.filter(
        (item) => item.conversation_id !== current.conversation_id
      );
      setConversations(remaining);
      if (remaining.length > 0) {
        await selectConversation(remaining[0]);
      } else {
        setCurrent(null);
        setEvents([]);
        setModuleStack([]);
        setProposals([]);
      }
    } catch (err) {
      setError(errorMessage(err, "无法删除对话"));
    } finally {
      setLoading(false);
    }
  }

  function updateConversationSummary(conversation: Conversation) {
    setConversations((items) =>
      [conversation, ...items.filter(
        (item) => item.conversation_id !== conversation.conversation_id
      )]
    );
  }

  return (
    <AuthGuard>
      <PageHeader
        title="统一对话"
        description="普通支持和所有练习模块共享同一段历史；模型只提出选项，由你决定是否进入。"
      />
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Panel title="对话历史">
          <div className="space-y-3">
            <Button type="button" onClick={requestNewConversation} disabled={loading}>
              新建对话
            </Button>
            <div className="max-h-[620px] space-y-2 overflow-y-auto">
              {conversations.map((conversation) => (
                <button
                  key={conversation.conversation_id}
                  type="button"
                  onClick={() => void selectConversation(conversation)}
                  className={`w-full rounded-md border p-3 text-left ${
                    current?.conversation_id === conversation.conversation_id
                      ? "border-brand bg-emerald-50"
                      : "border-line bg-white hover:border-brand"
                  }`}
                >
                  <div className="truncate text-sm font-medium text-ink">
                    {conversation.title}
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                    <span>{new Date(conversation.updated_at).toLocaleString()}</span>
                    {conversation.status === "archived" ? <span>已归档</span> : null}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </Panel>

        <div className="space-y-4">
          {showNotice ? (
            <HistoryNotice
              acknowledged={noticeAcknowledged}
              onAcknowledged={setNoticeAcknowledged}
              onConfirm={() => void createConversation()}
              onCancel={() => setShowNotice(false)}
              loading={loading}
            />
          ) : null}

          <ErrorBox message={error} />
          <Panel title={current?.title ?? "对话"}>
            {current ? (
              <>
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <ModuleBreadcrumb stack={moduleStack} />
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="secondary" onClick={renameConversation}>
                      重命名
                    </Button>
                    <Button type="button" variant="secondary" onClick={archiveConversation}>
                      {current.status === "archived" ? "取消归档" : "归档"}
                    </Button>
                    <Button type="button" variant="secondary" onClick={exportConversation}>
                      导出
                    </Button>
                    <Button type="button" variant="danger" onClick={deleteConversation}>
                      删除
                    </Button>
                  </div>
                </div>
                {moduleStack.length > 0 ? (
                  <div className="mb-3 flex flex-wrap gap-2 rounded-md border border-amber-200 bg-amber-50 p-3">
                    <Button type="button" variant="secondary" onClick={terminateCurrentModule}>
                      结束当前模块
                    </Button>
                    <Button type="button" variant="danger" onClick={terminateAllModules}>
                      结束全部模块
                    </Button>
                  </div>
                ) : null}
                <div className="min-h-[420px] space-y-3 rounded-md border border-line bg-panel p-3">
                  {events.length === 0 ? (
                    <EmptyState
                      title="开始这段对话"
                      description="只有你确认后才会进入角色扮演、结构化反思或分级练习。"
                    />
                  ) : (
                    events.map((item) => (
                      <TimelineEvent
                        key={item.event_id}
                        event={item}
                        proposal={proposalForEvent(item, proposals)}
                        loading={loading}
                        onAccept={acceptProposal}
                        onReject={rejectProposal}
                      />
                    ))
                  )}
                  {nextCursor ? (
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => void loadMoreEvents()}
                      disabled={loading}
                    >
                      载入更多历史
                    </Button>
                  ) : null}
                  {streamedResponse ? (
                    <div
                      className="mr-auto max-w-[92%] rounded-lg border border-line bg-white p-3"
                      aria-live="polite"
                    >
                      <div className="mb-1 text-xs text-slate-500">
                        {persistenceNotice
                          ? "SocialEase · 临时安全指引"
                          : "SocialEase · 正在回复"}
                      </div>
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
                        {streamedResponse}
                      </p>
                    </div>
                  ) : null}
                  {persistenceNotice ? (
                    <div className="text-sm text-amber-800" role="status">
                      {persistenceNotice}
                    </div>
                  ) : null}
                  {loading ? (
                    <div className="text-sm text-slate-500" role="status">
                      正在更新安全对话…
                    </div>
                  ) : null}
                </div>
                <form onSubmit={handleSubmit} className="mt-3 space-y-3">
                  <TextArea
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    placeholder={
                      moduleStack.length > 0
                        ? `继续 ${moduleLabel(moduleStack[moduleStack.length - 1].module_type)}…`
                        : "描述一个社交压力场景或继续普通对话…"
                    }
                    disabled={loading || current.status === "archived"}
                  />
                  <div className="flex items-center justify-between gap-3">
                    <span />
                    <Button
                      type="submit"
                      disabled={loading || current.status === "archived"}
                    >
                      {loading ? "处理中…" : "发送"}
                    </Button>
                  </div>
                </form>
              </>
            ) : (
              <EmptyState
                title={loading ? "正在载入" : "还没有对话"}
                description="新建对话前会先说明历史保存方式。历史不会自动成为 Agent Memory。"
              />
            )}
          </Panel>
        </div>
      </div>
    </AuthGuard>
  );
}

function HistoryNotice({
  acknowledged,
  onAcknowledged,
  onConfirm,
  onCancel,
  loading
}: {
  acknowledged: boolean;
  onAcknowledged: (value: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
  loading: boolean;
}) {
  return (
    <Panel title="对话历史持久化说明">
      <div className="space-y-3 text-sm leading-6 text-slate-700">
        <p>
          对话历史默认长期保存，直到你主动删除；你可以随时查看、导出或删除单段及全部历史。
          用于回复的模型上下文有长度上限，完整历史不会全部发送给模型。
        </p>
        <p>
          保存聊天历史不等于保存为 Agent Memory。长期偏好和练习摘要仍由设置页中的独立授权控制。
        </p>
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => onAcknowledged(event.target.checked)}
            className="mt-1"
          />
          <span>我已了解上述保存、导出和删除方式。</span>
        </label>
        <div className="flex gap-2">
          <Button type="button" onClick={onConfirm} disabled={!acknowledged || loading}>
            了解并新建
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel}>
            取消
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function ModuleBreadcrumb({ stack }: { stack: ModuleRun[] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      <Badge tone="info">普通对话</Badge>
      {stack.map((run) => (
        <span key={run.module_run_id} className="flex items-center gap-2">
          <span>›</span>
          <Badge tone={run.status === "active" ? "good" : "warn"}>
            {moduleLabel(run.module_type)}
          </Badge>
        </span>
      ))}
    </div>
  );
}

function TimelineEvent({
  event,
  proposal,
  loading,
  onAccept,
  onReject
}: {
  event: ConversationEvent;
  proposal: ModuleProposal | null;
  loading: boolean;
  onAccept: (proposal: ModuleProposal) => Promise<void>;
  onReject: (proposal: ModuleProposal) => Promise<void>;
}) {
  const lifecycle = isLifecycleEvent(event.event_type);
  const user = event.role === "user";
  return (
    <div
      className={`rounded-lg border p-3 ${
        lifecycle
          ? "mx-auto max-w-[92%] border-slate-200 bg-slate-50"
          : user
            ? "ml-auto max-w-[86%] border-brand bg-white"
            : event.event_type === "crisis_escalated"
              ? "mr-auto max-w-[92%] border-red-300 bg-red-50"
              : "mr-auto max-w-[92%] border-line bg-white"
      }`}
    >
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-slate-500">
        <span>{eventLabel(event)}</span>
        <span>{new Date(event.created_at).toLocaleTimeString()}</span>
      </div>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800">
        {event.content}
      </p>
      {proposal ? (
        <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 p-3">
          <div className="font-medium text-emerald-950">
            是否进入{moduleLabel(proposal.proposed_module)}？
          </div>
          <p className="mt-1 text-xs leading-5 text-emerald-900">
            进入后仍使用当前对话和历史；模块只会在你点击确认后启动，也可以随时手动结束。
          </p>
          <div className="mt-3 flex gap-2">
            <Button type="button" onClick={() => void onAccept(proposal)} disabled={loading}>
              确认进入
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => void onReject(proposal)}
              disabled={loading}
            >
              继续普通对话
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function proposalForEvent(
  event: ConversationEvent,
  proposals: ModuleProposal[]
): ModuleProposal | null {
  if (event.event_type !== "module_proposed") {
    return null;
  }
  const payload = event.structured_payload;
  if (payload?.kind !== "module_proposal") {
    return null;
  }
  const proposalId = payload.proposal_id;
  return (
    proposals.find((proposal) => proposal.proposal_id === proposalId) ?? null
  );
}

function mergeEvents(
  current: ConversationEvent[],
  appended: ConversationEvent[]
): ConversationEvent[] {
  const byId = new Map(current.map((event) => [event.event_id, event]));
  appended.forEach((event) => byId.set(event.event_id, event));
  return Array.from(byId.values()).sort(
    (left, right) => left.sequence_no - right.sequence_no
  );
}

function mergeProposals(
  current: ModuleProposal[],
  appended: ModuleProposal[]
): ModuleProposal[] {
  const byId = new Map(current.map((proposal) => [proposal.proposal_id, proposal]));
  appended.forEach((proposal) => byId.set(proposal.proposal_id, proposal));
  return Array.from(byId.values());
}

function moduleLabel(moduleType: ModuleType): string {
  return {
    roleplay: "角色扮演",
    worksheet: "结构化反思",
    exposure: "分级练习",
    resource: "资源导航"
  }[moduleType];
}

function eventLabel(event: ConversationEvent): string {
  if (event.role === "user") {
    return "你";
  }
  if (event.event_type === "crisis_escalated") {
    return "安全升级";
  }
  if (isLifecycleEvent(event.event_type)) {
    return "模块状态";
  }
  return event.event_type === "module_message"
    ? "SocialEase · 模块"
    : "SocialEase";
}

function isLifecycleEvent(type: ConversationEvent["event_type"]): boolean {
  return [
    "module_started",
    "module_suspended",
    "module_resumed",
    "module_completed",
    "module_terminated"
  ].includes(type);
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `message-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

type CommandReceipt = {
  conversationId: string;
  idempotencyKey: string;
  transientResponse?: string;
};

function writeCommandReceipt(receipt: CommandReceipt) {
  const receipts = readCommandReceipts();
  receipts[receipt.conversationId] = receipt;
  window.localStorage.setItem(
    COMMAND_RECEIPT_KEY,
    JSON.stringify({ version: 2, receipts })
  );
}

function readCommandReceipt(conversationId: string): CommandReceipt | null {
  return readCommandReceipts()[conversationId] ?? null;
}

function readCommandReceipts(): Record<string, CommandReceipt> {
  const raw = window.localStorage.getItem(COMMAND_RECEIPT_KEY);
  if (!raw) {
    return {};
  }
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (isCommandReceipt(value)) {
      return { [value.conversationId]: value };
    }
    if (value.version !== 2 || !isRecord(value.receipts)) {
      throw new Error("invalid command receipt store");
    }
    const receipts: Record<string, CommandReceipt> = {};
    for (const candidate of Object.values(value.receipts)) {
      if (!isCommandReceipt(candidate)) {
        throw new Error("invalid command receipt");
      }
      receipts[candidate.conversationId] = candidate;
    }
    return receipts;
  } catch {
    window.localStorage.removeItem(COMMAND_RECEIPT_KEY);
    return {};
  }
}

function clearCommandReceipt(conversationId: string) {
  const receipts = readCommandReceipts();
  delete receipts[conversationId];
  if (Object.keys(receipts).length === 0) {
    window.localStorage.removeItem(COMMAND_RECEIPT_KEY);
    return;
  }
  window.localStorage.setItem(
    COMMAND_RECEIPT_KEY,
    JSON.stringify({ version: 2, receipts })
  );
}

function isCommandReceipt(value: unknown): value is CommandReceipt {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.conversationId === "string" &&
    typeof value.idempotencyKey === "string" &&
    (value.transientResponse === undefined ||
      (typeof value.transientResponse === "string" &&
        value.transientResponse.length <= 4000))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json"
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
