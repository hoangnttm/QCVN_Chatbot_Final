"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { admin, type AdminDocument, type IndexVersion, type IngestionRun } from "@/lib/admin";
import { DOMAIN_LABEL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { Modal } from "@/components/ui/Modal";
import { Drawer } from "@/components/ui/Drawer";
import { showToast } from "@/components/ui/Toast";
import { StatusBar } from "@/components/admin/ReviewBits";
import { when } from "@/components/ui/Kpi";

export default function AdminDocumentsPage() {
  const [docs, setDocs] = useState<AdminDocument[] | null>(null);
  const [versions, setVersions] = useState<IndexVersion[]>([]);
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [includeStubs, setIncludeStubs] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runsFor, setRunsFor] = useState<AdminDocument | null>(null);
  const [runs, setRuns] = useState<IngestionRun[] | null>(null);
  const [activate, setActivate] = useState<IndexVersion | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, v, s] = await Promise.all([
        admin.documents(includeStubs),
        admin.indexVersions(),
        admin.stats(),
      ]);
      setDocs(d);
      setVersions(v);
      setStats(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [includeStubs]);
  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!runsFor) {
      setRuns(null);
      return;
    }
    admin
      .ingestionRuns(runsFor.document_id)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [runsFor]);

  async function doActivate() {
    if (!activate) return;
    setBusy(true);
    try {
      const r = await admin.activateIndex(activate.label, "admin-ui");
      showToast(`Đã kích hoạt ${r.activated}${r.previous ? ` (thay ${r.previous})` : ""}; BM25 đã dựng lại`, "success", 6000);
      setActivate(null);
      await load();
    } catch (e) {
      showToast(`Không kích hoạt được: ${e instanceof Error ? e.message : String(e)}`, "error", 6000);
    } finally {
      setBusy(false);
    }
  }

  const active = versions.find((v) => v.is_active);

  return (
    <main className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">Văn bản & index</h1>
        <span className="text-sm text-slate-500">
          index active: <b>{active?.label ?? "không có"}</b>
        </span>
        <label className="ml-auto flex items-center gap-1 text-xs text-slate-600">
          <input type="checkbox" checked={includeStubs} onChange={(e) => setIncludeStubs(e.target.checked)} /> hiện văn bản tham chiếu (stub)
        </label>
      </div>
      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}

      <section className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="px-2 py-2">Văn bản</th>
              <th className="px-2 py-2">Tệp</th>
              <th className="px-2 py-2 text-right">Chunk / rule</th>
              <th className="px-2 py-2">Kiểm duyệt rule</th>
              <th className="px-2 py-2">Ingest gần nhất</th>
              <th className="px-2 py-2">Trong index active</th>
            </tr>
          </thead>
          <tbody>
            {(docs ?? []).map((d) => (
                <tr key={d.document_id} className="border-t border-slate-100 align-top">
                  <td className="px-2 py-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link href={`/documents/${encodeURIComponent(d.document_id)}`} className="font-medium text-sky-800 hover:underline">
                        {d.document_code}
                      </Link>
                      <StatusBadge status={d.text_status} />
                    </div>
                    <div className="text-xs text-slate-600">{d.title}</div>
                    <div className="text-[11px] text-slate-500">
                      {DOMAIN_LABEL[d.domain] ?? d.domain} · {d.ingest_scope}
                      {d.grammar && ` · ${d.grammar}`}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-xs">
                    {d.version ? (
                      <>
                        <div>{d.version.file_name}</div>
                        <div className="font-mono text-slate-500" title={d.version.sha256}>
                          {d.version.sha256.slice(0, 12)}… · {d.version.page_count ?? "?"} tr. · {Math.round(d.version.size_bytes / 1024)} KB
                        </div>
                      </>
                    ) : (
                      <span className="text-slate-400">–</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right text-xs">
                    {d.counts.evidence_chunks} / {d.counts.limit_rules}
                    {d.counts.monitoring_requirements > 0 && <div className="text-slate-500">{d.counts.monitoring_requirements} yêu cầu quan trắc</div>}
                  </td>
                  <td className="w-40 px-2 py-1.5">{d.counts.limit_rules > 0 ? <StatusBar by={d.review_by_status} /> : <span className="text-xs text-slate-400">–</span>}</td>
                  <td className="px-2 py-1.5 text-xs">
                    {d.last_ingestion ? (
                      <>
                        <div>{when(d.last_ingestion.loaded_at)}</div>
                        <div className="text-slate-500">
                          phủ {d.last_ingestion.report_summary.coverage_ratio != null ? `${Math.round(d.last_ingestion.report_summary.coverage_ratio * 100)} %` : "?"} ·{" "}
                          {d.last_ingestion.report_summary.warnings ?? 0} cảnh báo · {d.last_ingestion.report_summary.unresolved_entities ?? 0} thực thể chưa map
                        </div>
                        <button type="button" onClick={() => setRunsFor(d)} className="text-sky-700 hover:underline">
                          xem báo cáo
                        </button>
                      </>
                    ) : (
                      <span className="text-slate-400">chưa ingest</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-xs">
                    {d.in_active_index ? (
                      <span className="text-emerald-700">✓ đúng phiên bản</span>
                    ) : d.stale ? (
                      <span className="text-amber-700">⚠ index cũ (hash khác)</span>
                    ) : d.not_indexed ? (
                      <span className="text-rose-700">✗ chưa có trong index</span>
                    ) : (
                      <span className="text-slate-400">–</span>
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-2 font-semibold">Phiên bản index</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th className="pr-2 py-1">Nhãn</th>
                <th className="pr-2 py-1">Trạng thái</th>
                <th className="pr-2 py-1">Embedding</th>
                <th className="pr-2 py-1">Văn bản</th>
                <th className="pr-2 py-1">Tạo</th>
                <th className="pr-2 py-1">Kích hoạt</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id} className="border-t border-slate-100 align-top">
                  <td className="pr-2 py-1 font-mono text-xs">{v.label}</td>
                  <td className="pr-2 py-1">
                    <StatusBadge status={v.is_active ? "approved" : v.status === "building" ? "needs_recheck" : "pending"} label={v.status} />
                  </td>
                  <td className="pr-2 py-1 text-xs">
                    {v.embedding_model} · {v.embedding_dim}d
                  </td>
                  <td className="pr-2 py-1 text-xs">{Object.keys(v.document_hashes ?? {}).length}</td>
                  <td className="pr-2 py-1 text-xs">{when(v.created_at)}</td>
                  <td className="pr-2 py-1 text-xs">{when(v.activated_at)}</td>
                  <td className="py-1 text-right">
                    {!v.is_active && v.status !== "building" && (
                      <button type="button" onClick={() => setActivate(v)} className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50">
                        Kích hoạt
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {versions.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-3 text-center text-xs text-slate-500">
                    Chưa có phiên bản index (chạy scripts/build_index.py).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {stats && (
        <section className="rounded border border-slate-200 bg-white p-4">
          <h2 className="mb-2 font-semibold">Thống kê cơ sở dữ liệu</h2>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3 lg:grid-cols-4">
            {Object.entries(stats).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-slate-100 py-0.5">
                <span className="text-slate-500">{k}</span>
                <span className="font-mono">{v}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <Drawer open={runsFor !== null} onClose={() => setRunsFor(null)} title={`Lần ingest — ${runsFor?.document_code ?? ""}`}>
        {runs === null ? (
          <p className="text-xs text-slate-500">Đang tải…</p>
        ) : runs.length === 0 ? (
          <p className="text-xs text-slate-500">Chưa có.</p>
        ) : (
          <div className="space-y-3">
            {runs.map((r) => (
              <details key={r.id} open={r.id === runs[0].id} className="rounded border border-slate-200 p-2 text-xs">
                <summary className="cursor-pointer">
                  #{r.id} · {when(r.loaded_at)} · <span className="font-mono">{r.file_hash.slice(0, 12)}…</span>
                </summary>
                <pre className="mt-2 max-h-[60vh] overflow-auto rounded bg-slate-50 p-2 text-[11px]">{JSON.stringify(r.report, null, 2)}</pre>
              </details>
            ))}
          </div>
        )}
      </Drawer>

      <Modal
        open={activate !== null}
        title={`Kích hoạt index ${activate?.label ?? ""}`}
        onClose={() => setActivate(null)}
        footer={
          <>
            <button type="button" onClick={() => setActivate(null)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
              Hủy
            </button>
            <button type="button" onClick={doActivate} disabled={busy} className="rounded bg-sky-700 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
              {busy ? "Đang kích hoạt…" : "Xác nhận kích hoạt"}
            </button>
          </>
        }
      >
        <p>
          Index đang active {active ? <b>{active.label}</b> : "(không có)"} sẽ chuyển sang <b>inactive</b>; BM25 trong bộ nhớ được dựng lại từ
          chunk hiện tại và mọi câu hỏi mới sẽ dùng phiên bản này. Thao tác được ghi vào audit.
        </p>
        {activate && (
          <p className="mt-2 text-xs text-slate-500">
            {activate.embedding_model} · {activate.embedding_dim}d · {Object.keys(activate.document_hashes ?? {}).length} văn bản · tạo {when(activate.created_at)}
            {activate.notes && ` · ${activate.notes}`}
          </p>
        )}
      </Modal>
    </main>
  );
}
