"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, message } from "@/features/stories/api";

export default function AuthPage() {
  const [password, setPassword] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const router = useRouter();
  return <section className="panel narrow"><p className="eyebrow">개인 작업실</p><h1>이야기를 이어갈 시간</h1>
    <p className="muted">서버에 설정한 작업실 암호로 로그인하세요. 공개 회원가입은 제공하지 않습니다.</p>
    <form className="stack" onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); setError("");
      try { await api("/api/auth/login", { password }); router.replace("/projects"); }
      catch (e) { setError(message(e)); } finally { setBusy(false); }
    }}><label>작업실 암호<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p role="alert" className="error">{error}</p>}<button disabled={busy}>{busy ? "확인 중…" : "작업실 열기"}</button></form>
  </section>;
}
