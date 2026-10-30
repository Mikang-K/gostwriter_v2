"use client";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { consumeGeneration } from "@/features/generation/client";
import { api, message, type Snapshot } from "@/features/stories/api";
import ProjectForm from "@/features/stories/ProjectForm";

type RequestInput = { projectId: string; expectedRevision: number; idempotencyKey: string;
  mode: "initial"|"continue"|"epilogue"; direction: string; provider: "ollama"|"openai"; allowCloud: boolean };
type Saved = { generationId: string; status: string; draftBody: string; draftComplete: boolean;
  errorCode: string|null; undoneAt: string|null };
const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);

export default function StoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [project, setProject] = useState<Snapshot|null>(null), [error, setError] = useState("");
  const [direction, setDirection] = useState(""), [draft, setDraft] = useState("");
  const [provider, setProvider] = useState<"ollama"|"openai">("ollama"), [allowCloud, setAllowCloud] = useState(false);
  const [status, setStatus] = useState("idle"), [busy, setBusy] = useState(false), [model, setModel] = useState("");
  const [selected, setSelected] = useState<string|null>(null), [body, setBody] = useState("");
  const [saved, setSaved] = useState<Saved|null>(null), [visibleGid, setVisibleGid] = useState<string|null>(null);
  const controller = useRef<AbortController|null>(null), generationId = useRef<string|null>(null);
  const running = useRef(false), mounted = useRef(true);
  const selectedId = useRef<string|null>(null);
  const key = `ghostwriter:generation:${id}`;
  const load = useCallback(async () => { const p = await api<Snapshot>(`/api/projects/${id}`); setProject(p); return p; }, [id]);
  const follow = useCallback(async (gid: string) => {
    generationId.current = gid; setVisibleGid(gid);
    for (let i = 0; i < 180 && mounted.current; i++) {
      const item = await api<Saved>(`/api/generations/${gid}`);
      if (!mounted.current) return;
      setSaved(item); setStatus(item.status);
      if (item.draftBody) setDraft(item.draftBody);
      if (terminal.has(item.status)) {
        if (item.status === "succeeded") sessionStorage.removeItem(key);
        if (item.status === "succeeded") { await load(); selectedId.current = null; setSelected(null); setBody(""); setDraft(""); }
        if (item.errorCode && item.status !== "cancelled") setError(message(new Error(item.errorCode)));
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    if (mounted.current) setError("상태 확인 시간이 초과되었습니다. 새로고침해 다시 확인해 주세요.");
  }, [key, load]);
  const send = useCallback(async (payload: RequestInput) => {
    controller.current = new AbortController();
    const response = await fetch("/api/generations", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: controller.current.signal });
    if (response.status === 202) { const replay = await response.json(); await follow(replay.generationId); return; }
    if (!response.ok) { const data = await response.json(); throw new Error(data.error?.code || "REQUEST_FAILED"); }
    await consumeGeneration(response, event => {
      if (!mounted.current) return;
      if (event.type === "accepted") {
        generationId.current = event.generationId; setVisibleGid(event.generationId);
        sessionStorage.setItem(key, JSON.stringify({ payload, generationId: event.generationId })); setStatus("streaming");
      } else if (event.type === "provider") setModel(`${event.provider} · ${event.model}`);
      else if (event.type === "delta") setDraft(text => text + event.text);
      else if (event.type === "phase") setStatus("finalizing");
      else if (event.type === "error") setError(message(new Error(event.code)));
      else if (event.type === "done") setStatus(event.status);
    }, controller.current.signal);
    if (generationId.current) await follow(generationId.current);
  }, [follow, key]);
  useEffect(() => {
    mounted.current = true;
    const start = setTimeout(() => {
    void load().catch(e => setError(message(e)));
    const raw = sessionStorage.getItem(key);
    if (raw) {
      const recover = async () => {
        try {
          const entry = JSON.parse(raw);
          if (!entry.payload || entry.payload.projectId !== id || !entry.payload.idempotencyKey) { sessionStorage.removeItem(key); return; }
          running.current = true; setBusy(true); setStatus("recovering");
          if (entry.generationId) await follow(entry.generationId); else await send(entry.payload);
        } catch (e) { if (mounted.current) setError(message(e)); }
        finally { running.current = false; if (mounted.current) setBusy(false); }
      }; void recover();
    }
    }, 0);
    return () => { clearTimeout(start); mounted.current = false; controller.current?.abort(); };
  }, [id, key, load, follow, send]);
  async function generate(mode: RequestInput["mode"]) {
    if (!project || running.current) return;
    const payload: RequestInput = { projectId: id, expectedRevision: project.revision,
      idempotencyKey: crypto.randomUUID(), mode, direction, provider, allowCloud };
    setAllowCloud(false);
    sessionStorage.setItem(key, JSON.stringify({ payload })); generationId.current = null; setVisibleGid(null);
    running.current = true; setBusy(true); setDraft(""); setSaved(null); setError(""); setStatus("connecting");
    try { await send(payload); }
    catch (e) {
      if (controller.current?.signal.aborted) setStatus("cancelled");
      else { setError(message(e)); if (generationId.current) await follow(generationId.current).catch(() => {}); else setStatus("failed"); }
    } finally { running.current = false; if (mounted.current) setBusy(false); }
  }
  async function cancel() {
    if (!generationId.current) return;
    try { await api(`/api/generations/${generationId.current}/cancel`, {}); controller.current?.abort(); setStatus("cancelled"); }
    catch (e) { setError(message(e)); await load(); }
  }
  async function command(action: "undo"|"finish") {
    if (!project || running.current) return; setError(""); setBusy(true); running.current = true;
    try { const p = await api<Snapshot>(`/api/projects/${id}/${action}`, { expectedRevision: project.revision });
      setProject(p); selectedId.current = null; setSelected(null); setBody(""); setSaved(null); setDraft(""); }
    catch (e) { setError(message(e)); } finally { setBusy(false); running.current = false; }
  }
  async function finalize() {
    if (!saved || !project || running.current) return;
    setBusy(true); running.current = true; setError(""); setStatus("finalizing");
    try { await api(`/api/generations/${saved.generationId}/finalize`, { expectedRevision: project.revision }); await follow(saved.generationId); }
    catch (e) { setError(message(e)); } finally { setBusy(false); running.current = false; }
  }
  if (!project) return <section className="panel"><p role="status">{error || "원고를 불러오고 있습니다…"}</p></section>;
  const activeEpisode = project.episodes.find(e => e.id === selected) || project.episodes.at(-1);
  const labels: Record<string, string> = { idle: "다음 장면을 준비하세요", connecting: "모델에 연결 중", streaming: "본문 생성 중",
    finalizing: "본문 생성 완료 · 상태 정리 중", succeeded: "원고 저장 완료", failed: "생성 실패 · 초안 확인", cancelled: "생성 중단",
    interrupted: "생성이 중단되었습니다", recovering: "저장된 생성 상태 확인 중", pending: "생성 준비 중", running: "생성 진행 중" };
  return <section><div className="section-head"><div><p className="eyebrow">{project.genre || "나의 이야기"} · {project.memory.isFinished ? "완결" : "연재 중"}</p><h1>{project.title}</h1></div><span className="badge">{project.episodes.length}회차</span></div>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="workspace"><aside className="panel stack"><h2>회차</h2>{!project.episodes.length && <p className="muted">아직 확정된 회차가 없습니다.</p>}
      {project.episodes.map(e => <button className={activeEpisode?.id === e.id ? "episode active" : "episode"} key={e.id} disabled={busy} onClick={async () => {
        selectedId.current = e.id; setSelected(e.id); setBody(""); if (e.body !== null) setBody(e.body); else try { const item = await api<{ body: string }>(`/api/projects/${id}/episodes/${e.id}`); if (selectedId.current === e.id) setBody(item.body); } catch (error) { if (selectedId.current === e.id) setError(message(error)); }
      }}>{e.episodeNumber}화<span>{e.summary.slice(0, 45)}</span></button>)}
      <details><summary>이야기 기억</summary><p>{project.memory.plotState || "첫 회차부터 기록됩니다."}</p><p className="muted">인물 상태 {project.memory.characterStates.length}개 · 복선 {project.memory.openForeshadowing.length}개</p></details>
      <button className="secondary" disabled={busy || !project.episodes.length} onClick={() => void command("undo")}>마지막 회차 되돌리기</button>
      {!project.memory.isFinished && <button className="quiet" disabled={busy} onClick={() => void command("finish")}>생성 없이 완결</button>}
      <details><summary>작품 설정</summary><fieldset disabled={busy}><ProjectForm key={project.revision} initial={project} onSaved={setProject} /></fieldset></details>
      <button className="quiet" disabled={busy} onClick={async () => { try { setProject(await api<Snapshot>(`/api/projects/${id}`, {
        title: project.title, genre: project.genre, language: project.language, notes: project.notes, worldBible: project.worldBible,
        styleExamples: project.styleExamples, targetEpisodes: project.targetEpisodes, characters: project.characters,
        expectedRevision: project.revision, archived: !project.archived }, "PATCH")); } catch (e) { setError(message(e)); }
      }}>{project.archived ? "보관 해제" : "작품 보관"}</button>
    </aside><div className="stack"><article className="panel manuscript"><p className="eyebrow">확정 원고 {activeEpisode ? `· ${activeEpisode.episodeNumber}화` : ""}</p>
      <div className="prose">{(selected ? body || activeEpisode?.body : activeEpisode?.body) || "첫 장면은 어떤 모습인가요? 아래에 방향을 적어 시작하세요."}</div></article>
      {(draft || busy) && <article className="panel draft"><p className="eyebrow">미확정 초안</p><div className="prose">{draft || "첫 문장을 기다리고 있습니다…"}</div></article>}
      <section className="panel stack"><div className="section-head"><h2>다음 장면</h2><span role="status" aria-live="polite" className="muted">{labels[status] || status}</span></div>
        {model && <p className="muted">{model}</p>}
        <label>전개 방향<textarea rows={4} value={direction} disabled={busy} onChange={e => setDirection(e.target.value)} placeholder="예: 하린이 동료의 거짓말을 눈치챈다. 하린의 시점으로 행동과 대사를 중심으로 쓰고, 추궁하기 직전에 끝낸다. 400자 내외." /></label>
        {!busy && project.memory.nextOptions.map((option, i) => <button className="suggestion" key={i} onClick={() => setDirection(option)}>{option}</button>)}
        <div className="row"><label>생성 모델<select disabled={busy} value={provider} onChange={e => setProvider(e.target.value as "ollama"|"openai")}><option value="ollama">로컬 모델</option><option value="openai">GPT (OpenAI)</option></select></label>
          <label className="check"><input type="checkbox" checked={allowCloud} disabled={busy} onChange={e => setAllowCloud(e.target.checked)} />이번 요청의 외부 모델 사용 허용</label></div>
        <p className="notice">외부 모델 사용을 허용하면 작품 설정·인물·최근 원문·요약·지시·생성 본문이 생성 또는 상태 정리를 위해 전송될 수 있습니다. 비용이 발생할 수 있습니다. 원고는 연결한 PostgreSQL에 저장됩니다.</p>
        <div className="actions"><button disabled={busy || project.memory.isFinished || project.archived || (provider === "openai" && !allowCloud)} onClick={() => void generate(project.episodes.length ? "continue" : "initial")}>{project.episodes.length ? "다음 회차 생성" : "첫 회차 생성"}</button>
          {project.episodes.length > 0 && !project.memory.isFinished && <button className="secondary" disabled={busy} onClick={() => void generate("epilogue")}>에필로그 생성</button>}
          {busy && <button className="danger" disabled={!visibleGid} onClick={() => void cancel()}>생성 중단</button>}
          {saved?.draftComplete && ["failed", "interrupted"].includes(saved.status) && <button className="secondary" disabled={busy} onClick={() => void finalize()}>상태 저장 재시도</button>}
        </div>{project.memory.isFinished && <p className="muted">완결된 작품입니다. 마지막 회차를 되돌리면 해당 회차 이전 상태로 돌아갑니다.</p>}
      </section></div></div>
  </section>;
}
