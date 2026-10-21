"use client";

import { useCallback, useEffect, useState } from "react";
import { admin, type AlertEvent, type HealthDetails, type Metrics } from "@/lib/admin";
import { Kpi, ms, pct, usd, when } from "@/components/ui/Kpi";
import { showToast } from "@/components/ui/Toast";

const WINDOWS = [
  { value: 15, label: "15 phút" },
  { value: 60, label: "1 giờ" },
  { value: 1440, label: "24 giờ" },
];

const SEVERITY: Record<string, string> = {
  info: "bg-sky-100 text-sky-800 border-sky-300",
  warn: "bg-amber-100 text-amber-900 border-amber-300",
  crit: "bg-rose-100 text-rose-800 border-rose-300",
};

const RULE_LABEL: Record<string, string> = {
  error_rate: "Tỷ lệ lỗi",
  abstain_rate: "Tỷ lệ abstain",
  validator_reject_rate: "Validator từ chối",
  p95_latency: "Độ trễ p95",
  gemini_consecutive_errors: "Gemini lỗi liên tiếp",
  budget_80: "Ngân sách 80 %",
  budget_100: "Vượt ngân sách",
  client_quota_exceeded: "Client vượt quota",
  health_degraded: "Hệ thống suy giảm",
  test: "Cảnh báo thử",
};

function Check({ ok, label, detail }: { ok: boolean | null; label: string; detail?: React.ReactNode }) {
  const color = ok === null ? "bg-slate-300" : ok ? "bg-emerald-500" : "bg-rose-500";
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />
      <div className="min-w-0">
        <div className="font-medium">{label}</div>
        {detail && <div className="text-xs text-slate-500">{detail}</div>}
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const [window_, setWindow] = useState(60);
  const [health, setHealth] = useState<HealthDetails | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [resolved, setResolved] = useState<AlertEvent[]>([]);
  const [showResolved, setShowResolved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const [h, m, a] = await Promise.all([admin.health(probe), admin.metrics(window_), admin.alerts(true)]);
      setHealth(h);
      setMetrics(m);
      setAlerts(a);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [probe, window_]);

  useEffect(() => {
    void load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 30_000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (!showResolved) return;
    admin
      .alerts(false)
      .then((all) => setResolved(all.filter((a) => a.resolved_at)))
      .catch(() => setResolved([]));
  }, [showResolved, alerts.length]);

  async function resolve(a: AlertEvent) {
    try {
      await admin.resolveAlert(a.id, "admin-ui");
      showToast("Đã đánh dấu xử lý", "success");
      await load();
    } catch (e) {
      showToast(`Không xử lý được: ${e instanceof Error ? e.message : String(e)}`, "error");
    }
  }

  async function testAlert() {
    try {
      const r = await admin.testAlert();
      showToast(r.sent ? "Đã gửi cảnh báo thử tới webhook" : `Không gửi được: ${r.error}`, r.sent ? "success" : "error", 6000);
    } catch (e) {
      showToast(`Lỗi: ${e instanceof Error ? e.message : String(e)}`, "error");
    }
  }

  const g = health?.checks.gemini;
  const budgetRatio = metrics?.budget.ratio ?? 0;
  const budgetTone = budgetRatio >= 1 ? "bad" : budgetRatio >= 0.8 ? "warn" : "good";

  return (
    <main className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Tổng quan vận hành</h1>
        <div className="flex gap-1 text-xs">
          {WINDOWS.map((w) => (
            <button
              key={w.value}
              type="button"
              onClick={() => setWindow(w.value)}
              className={`rounded border px-2 py-1 ${
                window_ === w.value ? "border-sky-500 bg-sky-50 text-sky-800" : "border-slate-300"
              }`}
            >
              {w.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-xs text-slate-600">
          <input type="checkbox" checked={probe} onChange={(e) => setProbe(e.target.checked)} /> probe Neo4j/Gemini
        </label>
        <span className="ml-auto flex items-center gap-2 text-xs text-slate-500">
          {updatedAt && <span>cập nhật {updatedAt.toLocaleTimeString("vi-VN")} · tự làm mới 30 s</span>}
          <button type="button" onClick={() => void load()} className="rounded border border-slate-300 px-2 py-1">
            Làm mới
          </button>
          <button type="button" onClick={testAlert} className="rounded border border-slate-300 px-2 py-1">
            Gửi cảnh báo thử
          </button>
        </span>
      </div>

      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}

      <section className="rounded border border-slate-200 bg-white p-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="font-semibold">Health</h2>
          {health && (
            <span
              className={`rounded border px-2 py-0.5 text-xs ${
                health.status === "ok" ? "border-emerald-300 bg-emerald-50 text-emerald-800" : "border-rose-300 bg-rose-50 text-rose-800"
              }`}
            >
              {health.status}
            </span>
          )}
          {health && (
            <span className="text-xs text-slate-500">
              v{health.version} · uptime {Math.round(health.uptime_s / 60)} phút · {health.alerts_active} cảnh báo đang mở
            </span>
          )}
        </div>
        {health ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Check ok={health.checks.database.ok} label="PostgreSQL" detail={`${health.checks.database.latency_ms} ms`} />
            <Check
              ok={health.checks.index.ok}
              label="Index active"
              detail={
                health.checks.index.active_label
                  ? `${health.checks.index.active_label} · BM25 ${health.checks.index.bm25_loaded ? `${health.checks.index.bm25_docs ?? "?"} chunk` : "chưa nạp"}`
                  : "không có index active"
              }
            />
            <Check
              ok={g ? (g.configured ? g.consecutive_errors === 0 : false) : null}
              label="Gemini"
              detail={
                g
                  ? g.configured
                    ? `${g.model} · thành công gần nhất ${when(g.last_success_at)} · lỗi liên tiếp ${g.consecutive_errors}${g.last_error ? ` · ${g.last_error}` : ""}${g.probe ? ` · probe ${g.probe.ok ? "ok" : g.probe.error}` : ""}`
                    : "chưa cấu hình GEMINI_API_KEY"
                  : undefined
              }
            />
            <Check
              ok={health.checks.neo4j.skipped ? null : health.checks.neo4j.ok}
              label="Neo4j"
              detail={health.checks.neo4j.skipped ? "bỏ qua (bật probe để kiểm)" : health.checks.neo4j.error ?? "ok"}
            />
          </div>
        ) : (
          <p className="text-sm text-slate-500">Đang tải…</p>
        )}
      </section>

      {metrics && (
        <section className="space-y-3">
          <h2 className="font-semibold">
            Chỉ số {WINDOWS.find((w) => w.value === metrics.window_min)?.label ?? `${metrics.window_min} phút`} gần nhất
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi
              label="Câu hỏi"
              value={metrics.questions}
              hint={Object.entries(metrics.by_status)
                .map(([k, v]) => `${k} ${v}`)
                .join(" · ")}
            />
            <Kpi
              label="Tỷ lệ lỗi"
              value={pct(metrics.error_rate)}
              tone={metrics.error_rate > 0.2 ? "bad" : metrics.error_rate > 0.05 ? "warn" : "good"}
              hint={`${metrics.by_status.error ?? 0} lỗi`}
            />
            <Kpi
              label="Tỷ lệ abstain"
              value={pct(metrics.abstain_rate)}
              tone={metrics.abstain_rate > 0.6 ? "warn" : "neutral"}
              hint={`${metrics.by_status.abstained ?? 0} không kết luận`}
            />
            <Kpi
              label="Validator từ chối"
              value={pct(metrics.validator_reject_rate)}
              tone={metrics.validator_reject_rate > 0.3 ? "warn" : "neutral"}
              hint={`trên ${metrics.llm_questions} câu dùng LLM`}
            />
            <Kpi label="Độ trễ p50 / p95" value={`${ms(metrics.latency_ms.p50)} / ${ms(metrics.latency_ms.p95)}`} hint={`max ${ms(metrics.latency_ms.max)}`} />
            <Kpi
              label="Gemini"
              value={`${metrics.llm.llm_calls} gọi`}
              tone={metrics.llm.llm_consecutive_errors >= 3 ? "bad" : "neutral"}
              hint={`${metrics.llm.llm_errors} lỗi từ khi khởi động · lỗi liên tiếp ${metrics.llm.llm_consecutive_errors}`}
            />
            <Kpi
              label="Phản hồi"
              value={`👍 ${metrics.feedback.positive} · 👎 ${metrics.feedback.negative}`}
              hint="trong cửa sổ đang chọn"
            />
            <Kpi label="Chi phí cửa sổ" value={usd(metrics.cost_usd.window)} hint={`tháng này ${usd(metrics.cost_usd.month, 2)}`} />
          </div>

          <div className="rounded border border-slate-200 bg-white p-4">
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="font-medium">Ngân sách Gemini hôm nay</span>
              <span className={budgetTone === "bad" ? "text-rose-700" : budgetTone === "warn" ? "text-amber-700" : "text-slate-600"}>
                {usd(metrics.budget.spent_usd)} / {usd(metrics.budget.limit_usd, 2)} ({pct(metrics.budget.ratio)})
              </span>
            </div>
            <div className="h-3 w-full overflow-hidden rounded bg-slate-100">
              <div
                className={`h-full ${budgetTone === "bad" ? "bg-rose-500" : budgetTone === "warn" ? "bg-amber-500" : "bg-emerald-500"}`}
                style={{ width: `${Math.min(100, Math.round(budgetRatio * 100))}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Chỉ cảnh báo, không chặn (quyết định PO). Hạn mức {metrics.quota.per_client_limit} câu/ngày/trình duyệt.
            </p>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Client vượt quota hôm nay</div>
                {metrics.quota.clients_over_quota.length === 0 ? (
                  <p className="text-xs text-slate-500">Không có.</p>
                ) : (
                  <ul className="text-xs">
                    {metrics.quota.clients_over_quota.map((c) => (
                      <li key={c.client_key} className="flex justify-between border-t border-slate-100 py-1 font-mono">
                        <span>{c.client_key}</span>
                        <span>
                          {c.questions} câu · {usd(c.cost_usd)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Top client hôm nay</div>
                {metrics.top_clients_today.length === 0 ? (
                  <p className="text-xs text-slate-500">Chưa có câu hỏi hôm nay.</p>
                ) : (
                  <ul className="text-xs">
                    {metrics.top_clients_today.map((c) => (
                      <li key={c.client_key} className="flex justify-between border-t border-slate-100 py-1 font-mono">
                        <span>{c.client_key}</span>
                        <span>
                          {c.questions} câu · {usd(c.cost_usd)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        </section>
      )}

      <section className="rounded border border-slate-200 bg-white p-4">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">Cảnh báo đang mở ({alerts.length})</h2>
          <label className="ml-auto flex items-center gap-1 text-xs text-slate-600">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> hiện đã xử lý
          </label>
        </div>
        <AlertTable items={alerts} onResolve={resolve} />
        {showResolved && (
          <div className="mt-4">
            <h3 className="mb-1 text-sm font-semibold text-slate-600">Đã xử lý ({resolved.length})</h3>
            <AlertTable items={resolved} />
          </div>
        )}
      </section>
    </main>
  );
}

function AlertTable({ items, onResolve }: { items: AlertEvent[]; onResolve?: (a: AlertEvent) => void }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">Không có.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-500">
            <th className="pr-2">Mức</th>
            <th className="pr-2">Quy tắc</th>
            <th className="pr-2">Thông điệp</th>
            <th className="pr-2">Bắn lúc</th>
            <th className="pr-2">Webhook</th>
            {onResolve && <th />}
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id} className="border-t border-slate-100 align-top">
              <td className="pr-2 py-1">
                <span className={`rounded border px-1.5 py-0.5 text-xs ${SEVERITY[a.severity] ?? SEVERITY.info}`}>{a.severity}</span>
              </td>
              <td className="pr-2 py-1 whitespace-nowrap">{RULE_LABEL[a.rule] ?? a.rule}</td>
              <td className="pr-2 py-1">
                {a.message}
                {a.resolved_at && (
                  <div className="text-xs text-slate-500">
                    xử lý {when(a.resolved_at)} bởi {a.resolved_by}
                  </div>
                )}
              </td>
              <td className="pr-2 py-1 whitespace-nowrap text-xs text-slate-600">{when(a.fired_at)}</td>
              <td className="pr-2 py-1 text-xs">
                {a.notified ? "đã gửi" : a.notify_error ? <span className="text-rose-700">lỗi: {a.notify_error}</span> : "không gửi"}
              </td>
              {onResolve && (
                <td className="py-1 text-right">
                  <button
                    type="button"
                    onClick={() => onResolve(a)}
                    className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50"
                  >
                    Đã xử lý
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
