"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { admin, type QueryLogList } from "@/lib/admin";
import { DOMAIN_LABEL, INTENT_LABEL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { STATUS_UI } from "@/components/chat/AnswerView";
import { ms, usd, when } from "@/components/ui/Kpi";
import { showToast } from "@/components/ui/Toast";

const PAGE = 50;
const FILTER_KEYS = ["status", "path", "feedback", "q", "from", "to", "client_id"] as const;

export default function LogsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Đang tải…</p>}>
      <LogsInner />
    </Suspense>
  );
}

function LogsInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [data, setData] = useState<QueryLogList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [exporting, setExporting] = useState(false);

  const params = new URLSearchParams();
  for (const k of FILTER_KEYS) {
    const v = sp.get(k);
    if (v) params.set(k, v);
  }
  const offset = Number(sp.get("offset") ?? 0) || 0;
  params.set("limit", String(PAGE));
  params.set("offset", String(offset));
  const key = params.toString();

  useEffect(() => {
    const d: Record<string, string> = {};
    for (const k of FILTER_KEYS) d[k] = sp.get(k) ?? "";
    setDraft(d);
  }, [sp]);

  const load = useCallback(async () => {
    try {
      setData(await admin.queryLogs(new URLSearchParams(key)));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [key]);

  useEffect(() => {
    void load();
  }, [load]);

  function apply(next: Record<string, string>, nextOffset = 0) {
    const p = new URLSearchParams();
    for (const k of FILTER_KEYS) if (next[k]) p.set(k, next[k]);
    if (nextOffset) p.set("offset", String(nextOffset));
    router.push(`${pathname}?${p.toString()}`);
  }

  async function exportCsv() {
    setExporting(true);
    try {
      const p = new URLSearchParams(key);
      p.delete("limit");
      p.delete("offset");
      await admin.exportCsv(p);
      showToast("Đã tải CSV (UTF-8 BOM, mở được bằng Excel)", "success");
    } catch (e) {
      showToast(`Không xuất được: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setExporting(false);
    }
  }

  const total = data?.total ?? 0;
  const pageNo = Math.floor(offset / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <main className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Nhật ký câu hỏi</h1>
        <span className="text-sm text-slate-500">{total} bản ghi</span>
        <button
          type="button"
          onClick={exportCsv}
          disabled={exporting}
          className="ml-auto rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
        >
          {exporting ? "Đang xuất…" : "Xuất CSV (theo bộ lọc)"}
        </button>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          apply(draft);
        }}
        className="grid gap-2 rounded border border-slate-200 bg-white p-3 text-sm sm:grid-cols-2 lg:grid-cols-4"
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Trạng thái</span>
          <select value={draft.status ?? ""} onChange={(e) => setDraft({ ...draft, status: e.target.value })} className="rounded border border-slate-300 px-2 py-1">
            <option value="">Tất cả</option>
            <option value="answered">answered — có căn cứ</option>
            <option value="abstained">abstained — không kết luận</option>
            <option value="failed">failed — validator từ chối</option>
            <option value="error">error — lỗi hệ thống</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Đường đi</span>
          <select value={draft.path ?? ""} onChange={(e) => setDraft({ ...draft, path: e.target.value })} className="rounded border border-slate-300 px-2 py-1">
            <option value="">Tất cả</option>
            <option value="policy">policy</option>
            <option value="llm">llm</option>
            <option value="fallback">fallback</option>
            <option value="error">error</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Phản hồi</span>
          <select value={draft.feedback ?? ""} onChange={(e) => setDraft({ ...draft, feedback: e.target.value })} className="rounded border border-slate-300 px-2 py-1">
            <option value="">Tất cả</option>
            <option value="negative">👎 có phản hồi xấu</option>
            <option value="positive">👍 chỉ phản hồi tốt</option>
            <option value="any">có phản hồi</option>
            <option value="none">chưa có phản hồi</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Tìm trong câu hỏi</span>
          <input value={draft.q ?? ""} onChange={(e) => setDraft({ ...draft, q: e.target.value })} className="rounded border border-slate-300 px-2 py-1" placeholder="BOD, lò hơi…" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Từ ngày</span>
          <input type="date" value={draft.from ?? ""} onChange={(e) => setDraft({ ...draft, from: e.target.value })} className="rounded border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Đến ngày</span>
          <input type="date" value={draft.to ?? ""} onChange={(e) => setDraft({ ...draft, to: e.target.value })} className="rounded border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">Client id</span>
          <input value={draft.client_id ?? ""} onChange={(e) => setDraft({ ...draft, client_id: e.target.value })} className="rounded border border-slate-300 px-2 py-1 font-mono text-xs" placeholder="uuid" />
        </label>
        <div className="flex items-end gap-2">
          <button type="submit" className="rounded bg-sky-700 px-3 py-1.5 font-semibold text-white">
            Lọc
          </button>
          <button type="button" onClick={() => apply({})} className="rounded border border-slate-300 px-3 py-1.5">
            Xóa lọc
          </button>
        </div>
      </form>

      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}

      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="px-2 py-2">Thời điểm</th>
              <th className="px-2 py-2">Câu hỏi</th>
              <th className="px-2 py-2">Miền / ý định</th>
              <th className="px-2 py-2">Trạng thái</th>
              <th className="px-2 py-2">Đường đi</th>
              <th className="px-2 py-2 text-right">Trễ</th>
              <th className="px-2 py-2 text-right">Chi phí</th>
              <th className="px-2 py-2">Phản hồi</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((it) => {
              const ui = STATUS_UI[it.status] ?? STATUS_UI.failed;
              return (
                <tr key={it.query_id} className="border-t border-slate-100 align-top hover:bg-slate-50">
                  <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-600">{when(it.created_at)}</td>
                  <td className="px-2 py-1.5">
                    <Link href={`/admin/logs/${encodeURIComponent(it.query_id)}`} className="text-sky-800 hover:underline">
                      {it.question.length > 120 ? `${it.question.slice(0, 120)}…` : it.question}
                    </Link>
                    {it.error && <div className="text-xs text-rose-700">{it.error.slice(0, 160)}</div>}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-600">
                    {DOMAIN_LABEL[it.domain] ?? it.domain} · {INTENT_LABEL[it.intent] ?? it.intent}
                  </td>
                  <td className="px-2 py-1.5">
                    <StatusBadge status={ui.badge} label={ui.label} />
                    {it.validation_ok === false && <div className="text-[11px] text-rose-700">validator ✗</div>}
                  </td>
                  <td className="px-2 py-1.5 text-xs">
                    {it.path}
                    {it.attempts > 1 && ` ×${it.attempts}`}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right text-xs">{ms(it.latency_total_ms)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right text-xs">{it.cost_usd ? usd(it.cost_usd) : "–"}</td>
                  <td className="px-2 py-1.5 text-xs">
                    {it.feedback_count === 0 ? (
                      <span className="text-slate-400">–</span>
                    ) : (
                      <>
                        {it.feedback_min != null && it.feedback_min < 0 && "👎"}
                        {it.feedback_max != null && it.feedback_max > 0 && "👍"} {it.feedback_count > 1 && `×${it.feedback_count}`}
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={8} className="px-2 py-6 text-center text-sm text-slate-500">
                  Không có bản ghi khớp bộ lọc.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => apply(draft, Math.max(0, offset - PAGE))}
          className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
        >
          ← Trước
        </button>
        <span className="text-slate-600">
          Trang {pageNo} / {pages}
        </span>
        <button
          type="button"
          disabled={offset + PAGE >= total}
          onClick={() => apply(draft, offset + PAGE)}
          className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
        >
          Sau →
        </button>
      </div>
    </main>
  );
}
