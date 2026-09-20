import { expect, test } from "@playwright/test";

import { parseConversationStreamFrame } from "../lib/conversation-stream";

test("parses a typed conversation stream chunk", () => {
  expect(
    parseConversationStreamFrame(
      'event: response_chunk\ndata: {"type":"response_chunk","text":"hello"}'
    )
  ).toEqual({ type: "response_chunk", text: "hello" });
});

test("accepts a typed heartbeat frame", () => {
  expect(
    parseConversationStreamFrame(
      'event: response_heartbeat\ndata: {"type":"response_heartbeat"}'
    )
  ).toEqual({ type: "response_heartbeat" });
});

test("accepts the compact completed-event payload", () => {
  expect(
    parseConversationStreamFrame(
      'event: response_completed\ndata: {"type":"response_completed","result":{"conversation":{"conversation_id":"demo"},"appended_events":[],"active_module_stack":[],"pending_module_proposal":null,"transient_response":"safe guidance","crisis_persistence_status":"degraded_retry_required"}}'
    )
  ).toEqual({
    type: "response_completed",
    result: {
      conversation: { conversation_id: "demo" },
      appended_events: [],
      active_module_stack: [],
      pending_module_proposal: null,
      transient_response: "safe guidance",
      crisis_persistence_status: "degraded_retry_required"
    }
  });
});

test("rejects drift between the SSE event name and payload discriminator", () => {
  expect(() =>
    parseConversationStreamFrame(
      'event: response_error\ndata: {"type":"response_chunk","text":"hello"}'
    )
  ).toThrow("Streaming event name did not match its payload");
});

test("rejects an incomplete completed-event payload", () => {
  expect(() =>
    parseConversationStreamFrame(
      'event: response_completed\ndata: {"type":"response_completed","result":{"appended_events":[]}}'
    )
  ).toThrow("Streaming response contained an invalid payload");
});
