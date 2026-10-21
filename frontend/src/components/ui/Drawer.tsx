"use client";

import { useEffect } from "react";

/** Slide-over panel: right side on desktop, bottom sheet on phones (or forced with `side`). */
export function Drawer({
  open,
  onClose,
  title,
  children,
  side = "auto",
  width = "lg:w-[560px]",
}: {
  open: boolean;
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  side?: "auto" | "right" | "bottom" | "left";
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);
  if (!open) return null;
  const pos =
    side === "bottom"
      ? "inset-x-0 bottom-0 max-h-[85vh] w-full rounded-t-xl"
      : side === "left"
        ? "inset-y-0 left-0 h-full w-[min(92vw,420px)]"
        : side === "right"
          ? `inset-y-0 right-0 h-full w-[min(92vw,560px)] ${width}`
          : `inset-x-0 bottom-0 max-h-[85vh] w-full rounded-t-xl lg:inset-y-0 lg:right-0 lg:left-auto lg:h-full lg:max-h-none lg:w-[560px] lg:rounded-none ${width}`;
  return (
    <div className="fixed inset-0 z-40 print:hidden" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className={`absolute flex flex-col bg-white shadow-xl ${pos}`}>
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0 flex-1 text-sm font-semibold">{title}</div>
          <button type="button" onClick={onClose} aria-label="Đóng" className="text-slate-500 hover:text-slate-800">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 text-sm">{children}</div>
      </div>
    </div>
  );
}
