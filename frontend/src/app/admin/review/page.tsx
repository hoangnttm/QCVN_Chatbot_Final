"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { admin, type ReviewSummary } from "@/lib/admin";
import { when } from "@/components/ui/Kpi";
import { BulkApproveModal } from "@/components/admin/BulkApproveModal";
import { StatusBar } from "@/components/admin/ReviewBits";

const ITEM_TYPES = [
  { type: "transition_rules", label: "Quy tắc chuyển tiếp", hint: "điều khoản chuyển tiếp / lộ trình / hiệu lực" },
  { type: "document_relations", label: "Quan hệ văn bản (REPLACES / REPEALS)", hint: "quan hệ rủi ro cao giữa các văn bản" },
  { type: "monitoring_requirements", label: "Yêu cầu quan trắc (TT 10/2021)", hint: "tần suất, thông số, vị trí" },
];

export default function ReviewPage() {
  const [s, setS] = useState<ReviewSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bulkFor, setBulkFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setS(await admin.reviewSummary());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <main className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Kiểm duyệt dữ liệu trích xuất</h1>
        <p className="text-sm text-slate-600">
          Quy tắc PLAN.md §11: mỗi bảng ngưỡng phải có ≥ 10 % dòng (mẫu rủi ro cao) được duyệt &quot;Đúng&quot; trước khi duyệt cả bảng;
          dòng bị từ chối hoặc cần làm rõ sẽ chặn duyệt hàng loạt.
        </p>
      </div>
      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}
      {!s && !error && <p className="text-sm text-slate-500">Đang tải…</p>}

      {s && (
        <section className="grid gap-3 md:grid-cols-2">
          {s.tables.map((t) => {
            const pending = t.rules_by_status.pending ?? 0;
            const canBulk = t.all_sampled_approved && pending > 0;
            return (
              <div key={t.table_code} className="rounded border border-slate-200 bg-white p-4">
                <div className="mb-1 flex flex-wrap items-baseline gap-2">
                  <Link href={`/admin/review/${encodeURIComponent(t.table_code)}`} className="font-semibold text-sky-800 hover:underline">
                    {t.table_code}
                  </Link>
                  <span className="text-xs text-slate-500">{t.document_id}</span>
                </div>
                {t.title && <p className="mb-2 text-xs text-slate-600">{t.title}</p>}
                <StatusBar by={t.rules_by_status} />
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
                  <span>
                    {t.rows_total} dòng · {t.rules_total} rule
                  </span>
                  <span className={t.all_sampled_approved ? "text-emerald-700" : "text-amber-700"}>
                    mẫu đã duyệt {t.rows_reviewed} / cần {t.sample_required}
                  </span>
                  {t.blocking_rows.length > 0 && <span className="text-rose-700">{t.blocking_rows.length} dòng chặn</span>}
                  {t.last_decision_at && <span>quyết định gần nhất {when(t.last_decision_at)}</span>}
                </div>
                <div className="mt-3 flex gap-2">
                  <Link
                    href={`/admin/review/${encodeURIComponent(t.table_code)}?in_sample=true`}
                    className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50"
                  >
                    Duyệt mẫu
                  </Link>
                  <button
                    type="button"
                    disabled={!canBulk}
                    onClick={() => setBulkFor(t.table_code)}
                    title={
                      pending === 0 ? "Không còn rule chờ duyệt" : t.all_sampled_approved ? "" : "Chưa đủ mẫu được duyệt Đúng"
                    }
                    className="rounded bg-sky-700 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
                  >
                    Duyệt cả bảng ({pending} rule chờ)
                  </button>
                </div>
              </div>
            );
          })}
        </section>
      )}

      {s && (
        <section>
          <h2 className="mb-2 font-semibold">Đối tượng khác</h2>
          <div className="grid gap-3 md:grid-cols-3">
            {ITEM_TYPES.map((it) => {
              const by =
                it.type === "transition_rules"
                  ? s.transition_rules.by_status
                  : it.type === "document_relations"
                    ? s.document_relations.by_status
                    : s.monitoring_requirements.by_status;
              return (
                <Link key={it.type} href={`/admin/review/items/${it.type}`} className="rounded border border-slate-200 bg-white p-4 hover:border-sky-300">
                  <div className="font-semibold text-sky-800">{it.label}</div>
                  <p className="mb-2 text-xs text-slate-500">{it.hint}</p>
                  <StatusBar by={by} />
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {bulkFor && (
        <BulkApproveModal
          tableCode={bulkFor}
          onClose={() => setBulkFor(null)}
          onDone={() => {
            setBulkFor(null);
            void load();
          }}
        />
      )}
    </main>
  );
}
