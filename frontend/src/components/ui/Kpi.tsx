export function Kpi({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const ring = {
    neutral: "border-slate-200",
    good: "border-emerald-300",
    warn: "border-amber-300",
    bad: "border-rose-300",
  }[tone];
  return (
    <div className={`rounded border bg-white p-3 ${ring}`}>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function pct(v: number | null | undefined): string {
  return v == null ? "–" : `${Math.round(v * 100)} %`;
}

export function ms(v: number | null | undefined): string {
  return v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`;
}

export function usd(v: number | null | undefined, digits = 4): string {
  return v == null ? "–" : `${v.toFixed(digits)} USD`;
}

export function when(v: string | null | undefined): string {
  if (!v) return "–";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("vi-VN");
}
