"use client";
import { useState } from "react";
import { api, message, type Character, type ProjectInput, type Snapshot } from "./api";

const empty: ProjectInput = { title: "", genre: "", language: "ko", notes: "", worldBible: "",
  styleExamples: [], targetEpisodes: null, characters: [] };

export default function ProjectForm({ initial, onSaved }: { initial?: Snapshot; onSaved: (project: Snapshot) => void }) {
  const [value, setValue] = useState<ProjectInput>(initial ? {
    title: initial.title, genre: initial.genre, language: initial.language, notes: initial.notes,
    worldBible: initial.worldBible, styleExamples: initial.styleExamples, targetEpisodes: initial.targetEpisodes,
    characters: initial.characters,
  } : empty);
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  const field = <K extends keyof ProjectInput>(key: K, data: ProjectInput[K]) => setValue(v => ({ ...v, [key]: data }));
  function editCharacter(index: number, key: keyof Character, data: string) {
    field("characters", value.characters.map((c, i) => i === index ? { ...c, [key]: data } : c));
  }
  return <form className="stack" onSubmit={async event => {
    event.preventDefault(); if (saving) return; setSaving(true); setError("");
    try {
      const saved = await api<Snapshot>(initial ? `/api/projects/${initial.projectId}` : "/api/projects",
        initial ? { ...value, expectedRevision: initial.revision, archived: initial.archived } : value,
        initial ? "PATCH" : "POST");
      onSaved(saved);
    } catch (e) { setError(message(e)); } finally { setSaving(false); }
  }}>
    <label>작품 제목<input required value={value.title} onChange={e => field("title", e.target.value)} placeholder="이야기에 이름을 붙여 주세요" /></label>
    <div className="row"><label>장르<input value={value.genre} onChange={e => field("genre", e.target.value)} placeholder="예: 미스터리 판타지" /></label>
      <label>언어<select value={value.language} onChange={e => field("language", e.target.value as ProjectInput["language"])}><option value="ko">한국어</option><option value="en">English</option><option value="ja">日本語</option></select></label>
      <label>목표 회차<input type="number" min={1} max={10000} value={value.targetEpisodes ?? ""} onChange={e => field("targetEpisodes", e.target.value ? Number(e.target.value) : null)} placeholder="미정" /></label></div>
    <label>세계관<textarea value={value.worldBible} onChange={e => field("worldBible", e.target.value)} placeholder="이 세계에서 반드시 지켜야 하는 규칙" rows={4} /></label>
    <label>작가 노트<textarea value={value.notes} onChange={e => field("notes", e.target.value)} placeholder="전개와 문체에서 지킬 약속" rows={3} /></label>
    <fieldset className="stack"><legend>등장인물</legend>{value.characters.map((character, index) => <div className="character" key={character.id}>
      <div className="row"><label>이름<input required value={character.name} onChange={e => editCharacter(index, "name", e.target.value)} /></label>
        <label>성별<input value={character.gender} onChange={e => editCharacter(index, "gender", e.target.value)} /></label>
        <label>나이대<input value={character.ageGroup} onChange={e => editCharacter(index, "ageGroup", e.target.value)} /></label></div>
      <label>성격<input value={character.personality} onChange={e => editCharacter(index, "personality", e.target.value)} /></label>
      <label>말투<input value={character.speechStyle} onChange={e => editCharacter(index, "speechStyle", e.target.value)} /></label>
      <label>배경<textarea value={character.background} onChange={e => editCharacter(index, "background", e.target.value)} rows={2} /></label>
      {!initial && <button type="button" className="quiet" onClick={() => field("characters", value.characters.filter((_, i) => i !== index))}>인물 삭제</button>}
    </div>)}
      <button type="button" className="secondary" disabled={value.characters.length >= 10} onClick={() => field("characters", [...value.characters,
        { id: crypto.randomUUID(), name: "", gender: "", ageGroup: "", personality: "", speechStyle: "", background: "" }])}>인물 추가</button></fieldset>
    <details><summary>문체 참고 글</summary><div className="stack">{[0, 1, 2].map(i => <label key={i}>참고 글 {i + 1}<textarea rows={3} value={value.styleExamples[i] || ""} onChange={e => {
      const next = Array.from({ length: 3 }, (_, index) => value.styleExamples[index] || ""); next[i] = e.target.value; field("styleExamples", next);
    }} /></label>)}</div></details>
    {error && <p role="alert" className="error">{error}</p>}
    <button disabled={saving}>{saving ? "저장 중…" : initial ? "설정 저장" : "작품 만들기"}</button>
  </form>;
}
