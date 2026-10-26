import json
from uuid import uuid4

from app.context import build_context
from app.memory import extract_memory


async def test_partial_extraction_retains_established_memory_and_sanitizes_removed_character():
    first, removed = str(uuid4()), str(uuid4())
    memory = {"schemaVersion": 1, "characterStates": [{"characterId": first, "status": "성 안"},
              {"characterId": removed, "status": "옛 인물"}], "plotState": "왕관을 찾는다",
              "relationships": [{"subjectId": first, "objectId": removed, "tag": "동료", "intimacy": 1}],
              "openForeshadowing": [{"id": str(uuid4()), "text": "종소리", "characterIds": [first, removed]}],
              "nextOptions": ["종탑으로 간다"], "isFinished": False}
    snapshot = {"memory": memory, "characters": [{"id": first, "name": "하린"}]}

    async def collect(messages, schema):
        source = json.loads(messages[1]["content"].split("\n본문 자료:\n")[0])
        assert len(source["previous"]["characterStates"]) == 1
        assert source["previous"]["relationships"] == []
        assert sum(len(m["content"].encode()) for m in messages) <= 8192 - 1024 - 512
        return '{"summary":"복도를 걸었다"}'

    result, summary = await extract_memory(snapshot, "복도를 걸었다.", "continue", collect, 8192)
    assert summary == "복도를 걸었다"
    assert result["plotState"] == memory["plotState"] and result["nextOptions"] == memory["nextOptions"]
    assert result["openForeshadowing"][0]["characterIds"] == [first]
    assert result["characterStates"][0]["status"] == "성 안"
    assert len(memory["characterStates"]) == 2  # Immutable source is untouched.


def test_optional_wrappers_are_counted_in_context_budget():
    snapshot = {"memory": {}, "episodes": [{"id": "one", "body": "가"*900, "summary": "나"*500,
                                               "episodeNumber": 1}]}
    context = build_context(snapshot, "진행", "continue", 4096, 1024)
    size = sum(len(m["content"].encode()) for m in context["messages"])
    assert size == context["estimatedPromptTokens"] <= 4096 - 1024 - 512
