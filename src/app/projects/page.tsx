"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, message } from "@/features/stories/api";

type Project = { projectId: string; title: string; revision: number };
export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]), [error, setError] = useState("");
  const [archived, setArchived] = useState(false), [loaded, setLoaded] = useState(false);
  useEffect(() => { let live = true; api<Project[]>(`/api/projects?archived=${archived}`).then(data => {
    if (live) { setProjects(data); setLoaded(true); }
  }).catch(e => { if (live) setError(message(e)); }); return () => { live = false; }; }, [archived]);
  return <section><div className="section-head"><div><p className="eyebrow">나의 원고</p><h1>이야기 서재</h1><p className="muted">설정은 기억하고, 다음 장면에 집중하세요.</p></div><Link className="button" href="/projects/new">새 작품</Link></div>
    <label className="check"><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} />보관한 작품 보기</label>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="cards">{projects.map(p => <Link key={p.projectId} className="panel story-card" href={`/projects/${p.projectId}`}><span className="eyebrow">{archived ? "보관함" : "작업 중"}</span><h2>{p.title}</h2><span className="muted">작품 열기 · 수정 {p.revision}회</span></Link>)}</div>
    {loaded && !projects.length && <div className="panel empty"><h2>{archived ? "보관한 작품이 없습니다" : "첫 이야기를 시작해 보세요"}</h2><p className="muted">제목과 인물을 정하면 첫 장면을 함께 쓸 수 있습니다.</p><Link href="/projects/new">새 작품 만들기</Link></div>}
  </section>;
}
