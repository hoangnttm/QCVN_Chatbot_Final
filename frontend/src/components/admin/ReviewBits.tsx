/** Small shared pieces for the review pages: status colours/labels and the per-status progress bar. */

export const STATUS_COLOR: Record<string, string> = {
  approved: "bg-emerald-500",
  pending: "bg-slate-300",
  rejected: "bg-rose-500",
  needs_recheck: "bg-amber-500",
};
export const STATUS_VI: Record<string, string> = {
  approved: "đã duyệt",
  pending: "chờ",
  rejected: "từ chối",
  needs_recheck: "cần làm rõ",
};

export function StatusBar({ by }: { by: Record<string, number> }) {
  const total = Object.values(by).reduce((a, b) => a + b, 0) || 1;
  return (
    <div>
      <div className="flex h-2.5 w-full overflow-hidden rounded bg-slate-100">
        {(["approved", "needs_recheck", "rejected", "pending"] as const).map((s) => (
          <div key={s} className={STATUS_COLOR[s]} style={{ width: `${((by[s] ?? 0) / total) * 100}%` }} title={`${STATUS_VI[s]} ${by[s] ?? 0}`} />
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
        {(["approved", "needs_recheck", "rejected", "pending"] as const).map((s) => (
          <span key={s}>
            <span className={`mr-1 inline-block h-2 w-2 rounded-full ${STATUS_COLOR[s]}`} />
            {STATUS_VI[s]} {by[s] ?? 0}
          </span>
        ))}
      </div>
    </div>
  );
}
