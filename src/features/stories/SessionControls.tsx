"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "./api";

export default function SessionControls() {
  const pathname = usePathname(), router = useRouter();
  const [authenticated, setAuthenticated] = useState(false);
  useEffect(() => {
    let live = true;
    fetch("/api/auth/session", { cache: "no-store" }).then(response => { if (live) setAuthenticated(response.ok); }).catch(() => { if (live) setAuthenticated(false); });
    return () => { live = false; };
  }, [pathname]);
  if (!authenticated) return <Link href="/auth">로그인</Link>;
  return <button className="nav-button" onClick={async () => {
    await api("/api/auth/logout", {});
    for (const key of Object.keys(sessionStorage)) if (key.startsWith("ghostwriter:generation:")) sessionStorage.removeItem(key);
    setAuthenticated(false); router.replace("/auth"); router.refresh();
  }}>로그아웃</button>;
}
