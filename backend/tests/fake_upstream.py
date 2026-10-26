"""Deterministic local Ollama fixture: python -m tests.fake_upstream."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="Test-only Ollama upstream")
stats = {"calls": 0, "narrativeCalls": 0, "memoryCalls": 0, "cancelledRequests": 0}


@app.get("/api/tags")
async def tags():
    return {"models": [{"name": "test-local", "model": "test-local", "size": 1}]}


@app.get("/stats")
async def read_stats():
    return dict(stats)


@app.post("/api/chat")
async def chat(request: Request):
    payload = await request.json()
    memory = "format" in payload
    source = "\n".join(message.get("content", "") for message in payload.get("messages", []))
    stats["calls"] += 1
    stats["memoryCalls" if memory else "narrativeCalls"] += 1

    async def stream():
        finished = False
        try:
            if memory:
                update = {"summary": "하린이 비밀 문을 발견했다.", "characterStates": [],
                          "plotState": "비밀 문 발견", "openForeshadowing": [], "relationships": [],
                          "nextOptions": ["문 안으로 들어간다", "주변을 살핀다", "동료를 부른다"]}
                chunks = ["{invalid memory}" if "FAIL_MEMORY" in source else json.dumps(update, ensure_ascii=False)]
            else:
                chunks = ["하린은 낡은 문 앞에서 ", "천천히 숨을 골랐다.\n\n", "달빛이 비추자 ",
                          "숨겨진 문양이 드러났다. ", "이제 새로운 이야기가 시작된다."]
                if "FAIL_MEMORY" in source:
                    chunks.append(" FAIL_MEMORY")
            for chunk in chunks:
                await asyncio.sleep(.5 if "SLOW" in source else .15)
                yield json.dumps({"message": {"role": "assistant", "content": chunk}, "done": False},
                                 ensure_ascii=False) + "\n"
            finished = True
            yield json.dumps({"message": {"content": ""}, "done": True,
                              "done_reason": "length" if "LENGTH" in source and not memory else "stop",
                              "prompt_eval_count": 64, "eval_count": len(chunks) * 8,
                              "eval_duration": len(chunks) * 150000000, "load_duration": 1000000}) + "\n"
        finally:
            if not finished:
                stats["cancelledRequests"] += 1

    return StreamingResponse(stream(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=11435, access_log=False)
