import { expect, test } from "@playwright/test";

import { parseConversationStreamFrame } from "../lib/conversation-stream";

test("parses a typed conversation stream chunk", () => {
  expect(
    parseConversationStreamFrame(
      'event: response_chunk\ndata: {"type":"response_chunk","text":"hello"}'
    )
  ).toEqual({ type: "response_chunk", text: "hello" });
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
      'event: response_completed\ndata: {"type":"response_completed","result":{"response":"done"}}'
    )
  ).toThrow("Streaming response contained an invalid payload");
});
