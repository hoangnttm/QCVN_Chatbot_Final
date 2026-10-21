"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Hỏi đáp" },
  { href: "/documents", label: "Văn bản" },
  { href: "/applicability", label: "Đánh giá áp dụng" },
  { href: "/admin", label: "Quản trị" },
];

export function Nav() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const [documents, setDocuments] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    api
      .getCorpus()
      .then((c) => alive && setDocuments(c.documents.map((d) => d.short_code)))
      .catch(() => undefined); // the header works without the list
    return () => {
      alive = false;
    };
  }, []);
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
  return (
    <header className="border-b border-slate-200 bg-white print:hidden">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2.5 sm:px-6">
        <Link href="/" className="text-lg font-bold text-slate-900">
          QCVN Chatbot
        </Link>
        <button
          type="button"
          aria-label="Mở menu"
          onClick={() => setOpen((v) => !v)}
          className="ml-auto rounded border border-slate-300 px-2 py-1 text-sm md:hidden"
        >
          ☰
        </button>
        <nav
          className={`${open ? "flex" : "hidden"} w-full flex-col gap-1 text-sm md:flex md:w-auto md:flex-row md:gap-4`}
        >
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className={`rounded px-2 py-1 md:px-0 ${
                isActive(l.href) ? "font-semibold text-sky-800" : "text-slate-600 hover:underline"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <span className="hidden text-xs text-slate-500 lg:inline">{documents.join(" · ")}</span>
      </div>
    </header>
  );
}
