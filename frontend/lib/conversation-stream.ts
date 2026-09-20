/** Runtime validation for the conversation SSE protocol boundary. */

import type {
  ConversationStreamCompletedResult,
  ConversationStreamPayload
} from "./types";

export function parseConversationStreamFrame(
  frame: string
): ConversationStreamPayload {
  const lines = frame.split("\n");
  const eventNames = lines
    .filter((line) => line.startsWith("event:"))
    .map((line) => line.slice(6).trim());
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (eventNames.length !== 1 || !data) {
    throw new Error("Streaming response contained an invalid event");
  }

  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    throw new Error("Streaming response contained invalid JSON");
  }
  if (!isRecord(payload) || payload.type !== eventNames[0]) {
    throw new Error("Streaming event name did not match its payload");
  }
  if (payload.type === "response_chunk" && typeof payload.text === "string") {
    return { type: payload.type, text: payload.text };
  }
  if (
    payload.type === "response_completed" &&
    isRecord(payload.result) &&
    isRecord(payload.result.conversation) &&
    Array.isArray(payload.result.appended_events) &&
    Array.isArray(payload.result.active_module_stack) &&
    (payload.result.transient_response === null ||
      typeof payload.result.transient_response === "string") &&
    isCrisisPersistenceStatus(payload.result.crisis_persistence_status) &&
    (payload.result.pending_module_proposal === null ||
      isRecord(payload.result.pending_module_proposal))
  ) {
    return {
      type: payload.type,
      result: payload.result as ConversationStreamCompletedResult
    };
  }
  if (payload.type === "response_error" && typeof payload.detail === "string") {
    return { type: payload.type, detail: payload.detail };
  }
  if (payload.type === "response_heartbeat") {
    return { type: payload.type };
  }
  throw new Error("Streaming response contained an invalid payload");
}

function isCrisisPersistenceStatus(value: unknown): boolean {
  return [
    "not_applicable",
    "persisted",
    "degraded_retry_required",
    "safety_state_persisted_command_unconfirmed"
  ].includes(String(value));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
