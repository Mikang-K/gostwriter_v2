"use client";
import { useEffect, useState } from "react";
import { api, message } from "@/features/stories/api";

type Row = { generationId: string; status: string; createdAt: string; provider: string; model: string|null;
  metrics: { ttftMs?: number; generationMs?: number; providerTps?: number; retryCount?: number } };
export default function Metrics() {
  const [rows, setRows] = useState<Row[]>([]), [error, setError] = useState("");
  useEffect(() => { api<Row[]>("/api/metrics?days=7").then(setRows).catch(e => setError(message(e))); }, []);
  const format = (value: number|undefined, divisor = 1) => value == null ? "미측정" : (value/divisor).toFixed(1);
  return <section><p className="eyebrow">최근 7일</p><h1>생성 기록</h1><p className="muted">첫 응답·처리 시간·모델 출력 속도를 구분합니다. 적은 표본은 성능 보장이 아닙니다.</p>
    {error && <p role="alert" className="error">{error}</p>}<div className="panel table-wrap"><table><thead><tr><th>시각</th><th>모델</th><th>상태</th><th>첫 응답</th><th>처리 시간</th><th>출력 tokens/s</th><th>재시도</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.generationId}><td>{new Date(r.createdAt).toLocaleString("ko-KR")}</td><td>{r.provider}<br />{r.model || "미측정"}</td><td>{r.status}</td><td>{format(r.metrics.ttftMs, 1000)}초</td><td>{format(r.metrics.generationMs, 1000)}초</td><td>{format(r.metrics.providerTps)}</td><td>{r.metrics.retryCount ?? "미측정"}</td></tr>)}</tbody></table>{!rows.length && <p className="muted">아직 생성 기록이 없습니다.</p>}</div>
  </section>;
}
