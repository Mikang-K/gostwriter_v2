import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";
import SessionControls from "@/features/stories/SessionControls";

export const metadata: Metadata = {
  title: "Ghostwriter v2",
  description: "로컬 모델과 함께 쓰는 소설 작업실",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body><header className="topbar"><Link href="/projects" className="brand">ghostwriter<span>작은 이야기 작업실</span></Link><nav aria-label="주 메뉴"><Link href="/projects">서재</Link><Link href="/metrics">생성 기록</Link><SessionControls /></nav></header><main className="container">{children}</main><footer className="footer">Ghostwriter v2 · 원고와 설정을 이어 쓰는 개인 작업실</footer></body>
    </html>
  );
}
