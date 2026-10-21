import { STATUS_LABEL } from "@/lib/api";

const STYLE: Record<string, string> = {
  official: "bg-emerald-100 text-emerald-800 border-emerald-300",
  draft: "bg-amber-100 text-amber-900 border-amber-300",
  unverified: "bg-slate-100 text-slate-700 border-slate-300",
  approved: "bg-emerald-100 text-emerald-800 border-emerald-300",
  pending: "bg-slate-100 text-slate-700 border-slate-300",
  rejected: "bg-rose-100 text-rose-800 border-rose-300",
  needs_recheck: "bg-amber-100 text-amber-900 border-amber-300",
};

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${STYLE[status] ?? STYLE.pending}`}
    >
      {label ?? STATUS_LABEL[status] ?? status}
    </span>
  );
}
