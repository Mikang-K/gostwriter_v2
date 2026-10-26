export type StreamEvent =
  | { type: "accepted"; requestId: string; generationId: string }
  | { type: "provider"; provider: string; model: string; attempt: number }
  | { type: "delta"; text: string }
  | { type: "phase"; phase: "finalizing" }
  | { type: "committed"; episodeId: string; revision: number }
  | { type: "error"; code: string; retryable: boolean; draftAvailable: boolean }
  | { type: "cancelled" }
  | { type: "done"; status: string };

function eventFrom(value: unknown): StreamEvent {
  if (!value || typeof value !== "object") throw new Error("INVALID_STREAM");
  const v = value as Record<string, unknown>;
  const string = (key: string) => typeof v[key] === "string";
  const valid = v.type === "accepted" ? string("requestId") && string("generationId") :
    v.type === "provider" ? string("provider") && string("model") && typeof v.attempt === "number" :
    v.type === "delta" ? string("text") : v.type === "phase" ? v.phase === "finalizing" :
    v.type === "committed" ? string("episodeId") && typeof v.revision === "number" :
    v.type === "error" ? string("code") && typeof v.retryable === "boolean" && typeof v.draftAvailable === "boolean" :
    v.type === "done" ? string("status") : v.type === "cancelled";
  if (!valid) throw new Error("INVALID_STREAM");
  return v as StreamEvent;
}

export async function consumeGeneration(response: Response, onEvent: (event: StreamEvent) => void, signal: AbortSignal) {
  if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
    throw new Error("INVALID_STREAM");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "", done = false;
  const abort = () => { void reader.cancel(); };
  signal.addEventListener("abort", abort, { once: true });
  try {
    while (true) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const part = await reader.read();
      if (part.done) break;
      buffer = (buffer + decoder.decode(part.value, { stream: true })).replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const lines = frame.split("\n");
        const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        if (done) throw new Error("INVALID_STREAM");
        const event = eventFrom(JSON.parse(data));
        const tag = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
        if (tag && tag !== event.type) throw new Error("INVALID_STREAM");
        onEvent(event);
        if (event.type === "done") done = true;
      }
    }
    buffer += decoder.decode();
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (!done || buffer.trim()) throw new Error("STREAM_INTERRUPTED");
  } finally {
    signal.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
