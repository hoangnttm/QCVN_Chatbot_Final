"use client";

import { useEffect, useState } from "react";

type ToastMsg = { id: number; text: string; tone: "info" | "success" | "error" };
type Listener = (msgs: ToastMsg[]) => void;

let msgs: ToastMsg[] = [];
const listeners = new Set<Listener>();
let seq = 1;

function emit() {
  for (const l of listeners) l(msgs);
}

/** Show a transient message from anywhere (no context needed). */
export function showToast(text: string, tone: ToastMsg["tone"] = "info", ms = 3500): void {
  const id = seq++;
  msgs = [...msgs, { id, text, tone }];
  emit();
  setTimeout(() => {
    msgs = msgs.filter((m) => m.id !== id);
    emit();
  }, ms);
}

const TONE: Record<ToastMsg["tone"], string> = {
  info: "border-slate-300 bg-white text-slate-800",
  success: "border-emerald-300 bg-emerald-50 text-emerald-900",
  error: "border-rose-300 bg-rose-50 text-rose-900",
};

export function Toasts() {
  const [items, setItems] = useState<ToastMsg[]>([]);
  useEffect(() => {
    listeners.add(setItems);
    return () => {
      listeners.delete(setItems);
    };
  }, []);
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4 print:hidden">
      {items.map((m) => (
        <div key={m.id} className={`pointer-events-auto rounded border px-3 py-2 text-sm shadow ${TONE[m.tone]}`}>
          {m.text}
        </div>
      ))}
    </div>
  );
}
