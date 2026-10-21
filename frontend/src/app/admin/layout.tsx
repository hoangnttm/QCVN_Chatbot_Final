"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { admin, clearToken, getToken, setToken } from "@/lib/admin";
import { ApiError } from "@/lib/api";

const SUBNAV = [
  { href: "/admin", label: "Tổng quan", exact: true },
  { href: "/admin/logs", label: "Nhật ký" },
  { href: "/admin/review", label: "Kiểm duyệt" },
  { href: "/admin/documents", label: "Văn bản & index" },
];

type Auth = "checking" | "anonymous" | "ok";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/admin";
  const router = useRouter();
  const [auth, setAuth] = useState<Auth>("checking");
  const [version, setVersion] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const verify = useCallback(async () => {
    if (!getToken()) {
      setAuth("anonymous");
      return;
    }
    try {
      const r = await admin.ping();
      setVersion(r.version);
      setAuth("ok");
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setError("Backend chưa cấu hình ADMIN_TOKEN (biến môi trường).");
      }
      setAuth("anonymous");
    }
  }, []);

  useEffect(() => {
    void verify();
  }, [verify]);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    setToken(t);
    try {
      const r = await admin.ping();
      setVersion(r.version);
      setAuth("ok");
      setInput("");
    } catch (err) {
      clearToken();
      setError(
        err instanceof ApiError && err.status === 503
          ? "Backend chưa cấu hình ADMIN_TOKEN (biến môi trường)."
          : err instanceof ApiError && err.status === 401
            ? "Token không đúng."
            : `Không kết nối được backend: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    clearToken();
    setAuth("anonymous");
    router.push("/admin");
  }

  if (auth === "checking") {
    return <main className="mx-auto max-w-7xl p-6 text-sm text-slate-500">Đang kiểm tra phiên quản trị…</main>;
  }

  if (auth === "anonymous") {
    return (
      <main className="mx-auto w-full max-w-md px-4 py-10 sm:px-6">
        <h1 className="mb-1 text-xl font-bold">Khu vực quản trị</h1>
        <p className="mb-4 text-sm text-slate-600">
          Nhập token dùng chung (biến <code>ADMIN_TOKEN</code> của backend). Token được lưu trong trình duyệt này cho
          tới khi đăng xuất; đây là cơ chế tạm cho nội bộ/staging tới khi có tài khoản (M9).
        </p>
        <form onSubmit={login} className="space-y-3">
          <input
            type="password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Token quản trị"
            autoComplete="current-password"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
          />
          {error && <p className="text-sm text-rose-700">{error}</p>}
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded bg-sky-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy ? "Đang kiểm tra…" : "Đăng nhập"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-4 sm:px-6">
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 pb-3 text-sm">
        {SUBNAV.map((s) => {
          const active = s.exact ? pathname === s.href : pathname.startsWith(s.href);
          return (
            <Link
              key={s.href}
              href={s.href}
              className={active ? "font-semibold text-sky-800" : "text-slate-600 hover:underline"}
            >
              {s.label}
            </Link>
          );
        })}
        <span className="ml-auto flex items-center gap-3 text-xs text-slate-500">
          {version && <span>backend v{version}</span>}
          <button type="button" onClick={logout} className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50">
            Đăng xuất
          </button>
        </span>
      </div>
      {children}
    </div>
  );
}
