import { describe, expect, it } from "vitest";
import { consumeGeneration, type StreamEvent } from "../../src/features/generation/client";

function response(text: string, bytewise = false) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(c) {
    if (bytewise) for (const byte of bytes) c.enqueue(new Uint8Array([byte])); else c.enqueue(bytes);
    c.close();
  } }), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
}
describe("generation stream boundary", () => {
  it("preserves split Korean UTF-8, CRLF, comments and terminal frames", async () => {
    const events: StreamEvent[] = [];
    await consumeGeneration(response(': ping\r\n\r\nevent: delta\r\ndata: {"type":"delta","text":"한글 문장"}\r\n\r\ndata: {"type":"done","status":"succeeded"}\r\n\r\n', true), e => events.push(e), new AbortController().signal);
    expect(events).toEqual([{ type: "delta", text: "한글 문장" }, { type: "done", status: "succeeded" }]);
  });
  it.each([
    'data: {"type":"delta","text":"중단"}\n\n',
    'data: {"type":"delta","text":5}\n\n',
    'event: error\ndata: {"type":"done","status":"failed"}\n\n',
    'data: {"type":"done","status":"succeeded"}\n\ndata: {"type":"cancelled"}\n\n',
  ])("rejects interrupted or invalid frames", async text => {
    await expect(consumeGeneration(response(text), () => {}, new AbortController().signal)).rejects.toThrow();
  });
  it("propagates abort and cancels the reader", async () => {
    const controller = new AbortController(); let cancelled = false;
    const upstream = new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"문장"}\n\n')); }, cancel() { cancelled = true; } }), { headers: { "Content-Type": "text/event-stream" } });
    await expect(consumeGeneration(upstream, () => controller.abort(), controller.signal)).rejects.toThrow("Aborted");
    expect(cancelled).toBe(true);
  });
});
