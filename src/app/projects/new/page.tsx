"use client";
import { useRouter } from "next/navigation";
import ProjectForm from "@/features/stories/ProjectForm";

export default function NewPage() {
  const router = useRouter();
  return <section className="panel form-panel"><p className="eyebrow">새 원고</p><h1>이야기의 씨앗</h1><p className="muted">바뀌면 안 되는 설정부터 정해 주세요. 첫 회차는 작품을 만든 뒤 생성합니다.</p>
    <ProjectForm onSaved={p => router.push(`/projects/${p.projectId}`)} /></section>;
}
