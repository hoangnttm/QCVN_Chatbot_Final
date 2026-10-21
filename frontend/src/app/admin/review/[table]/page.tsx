"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { admin, type ReviewRow, type ReviewRowDetail, type ReviewTable } from "@/lib/admin";
import { StatusBadge } from "@/components/StatusBadge";
import { Drawer } from "@/components/ui/Drawer";
import { BulkApproveModal } from "@/components/admin/BulkApproveModal";
import { DecisionForm, DecisionHistory } from "@/components/admin/DecisionForm";
import { StatusBar, STATUS_VI } from "@/components/admin/ReviewBits";

export default function ReviewTablePage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Đang tải…</p>}>
      <Inner />
    </Suspense>
  );
}

function fmtValue(v: { op: string; value: number | null; min: number | null; max: number | null; ref_o2: number | null; raw: string }) {
  if (v.value != null) return `${v.op} ${v.value}`;
  if (v.min != null || v.max != null) return `${v.min ?? ""}–${v.max ?? ""}`;
  return v.raw || "–";
}

function Inner() {
  const { table } = useParams<{ table: string }>();
  const tableCode = decodeURIComponent(table);
  const sp = useSearchParams();
  const [data, setData] = useState<ReviewTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState(sp.get("status") ?? "");
  const [riskMin, setRiskMin] = useState(sp.get("risk_min") ?? "0");
  const [inSample, setInSample] = useState(sp.get("in_sample") === "true");
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewRowDetail | null>(null);
  const [bulk, setBulk] = useState(false);

  const load = useCallback(async () => {
    const p = new URLSearchParams();
    if (status) p.set("status", status);
    if (riskMin && riskMin !== "0") p.set("risk_min", riskMin);
    if (inSample) p.set("in_sample", "true");
    if (q) p.set("q", q);
    try {
      setData(await admin.reviewTable(tableCode, p));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [tableCode, status, riskMin, inSample, q]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!openRow) {
      setDetail(null);
      return;
    }
    setDetail(null);
    admin
      .reviewRow(openRow)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [openRow]);

  const rows = data?.rows ?? [];
  const idx = rows.findIndex((r) => r.row_id === openRow);
  const goNext = () => {
    if (idx >= 0 && idx + 1 < rows.length) setOpenRow(rows[idx + 1].row_id);
    else setOpenRow(null);
  };

  return (
    <main className="space-y-4">
      <Link href="/admin/review" className="text-sm text-sky-700 hover:underline">
        ← Kiểm duyệt
      </Link>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold">{tableCode}</h1>
          {data?.table.title && <p className="text-sm text-slate-600">{data.table.title}</p>}
          {data?.table.citation_label && <p className="text-xs text-slate-500">{data.table.citation_label}</p>}
        </div>
        {data && (
          <div className="w-full sm:w-72">
            <StatusBar by={data.rules_by_status} />
            <div className={`mt-1 text-xs ${data.sample.ok ? "text-emerald-700" : "text-amber-700"}`}>
              mẫu đã duyệt {data.sample.reviewed_rows} / cần {data.sample.required} ({data.sample.n} dòng trong mẫu)
              {data.sample.blocking_rows.length > 0 && ` · ${data.sample.blocking_rows.length} dòng chặn`}
            </div>
            <button
              type="button"
              disabled={!data.sample.ok || (data.rules_by_status.pending ?? 0) === 0}
              onClick={() => setBulk(true)}
              className="mt-2 rounded bg-sky-700 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
            >
              Duyệt cả bảng ({data.rules_by_status.pending ?? 0} rule chờ)
            </button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded border border-slate-200 bg-white p-3 text-sm">
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-slate-500">Trạng thái</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-slate-300 px-2 py-1">
            <option value="">Tất cả</option>
            <option value="pending">chờ</option>
            <option value="approved">đã duyệt</option>
            <option value="rejected">từ chối</option>
            <option value="needs_recheck">cần làm rõ</option>
            <option value="mixed">hỗn hợp</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-slate-500">Rủi ro ≥</span>
          <input type="number" min={0} value={riskMin} onChange={(e) => setRiskMin(e.target.value)} className="w-20 rounded border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={inSample} onChange={(e) => setInSample(e.target.checked)} /> chỉ dòng trong mẫu 10 %
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-slate-500">Tìm thông số</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="BOD, pH, bụi…" className="rounded border border-slate-300 px-2 py-1" />
        </label>
        <span className="ml-auto text-xs text-slate-500">{rows.length} dòng</span>
      </div>

      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}

      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="px-2 py-2">#</th>
              <th className="px-2 py-2">Thông số / ô gốc</th>
              <th className="px-2 py-2">Giá trị theo cột</th>
              <th className="px-2 py-2">Rủi ro</th>
              <th className="px-2 py-2">Mẫu</th>
              <th className="px-2 py-2">Trạng thái</th>
              <th className="px-2 py-2">Quyết định gần nhất</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.row_id}
                onClick={() => setOpenRow(r.row_id)}
                className={`cursor-pointer border-t border-slate-100 align-top hover:bg-sky-50 ${openRow === r.row_id ? "bg-sky-50" : ""}`}
              >
                <td className="px-2 py-1.5 text-xs text-slate-500">{r.row_no}</td>
                <td className="px-2 py-1.5">
                  <div className="font-medium">{r.cells[data?.table.columns[1] ?? ""] ?? Object.values(r.cells)[1] ?? r.row_id}</div>
                  <div className="text-xs text-slate-500">{r.citation_label}</div>
                  {r.parse_warnings.length > 0 && <div className="text-[11px] text-amber-700">⚠ {r.parse_warnings.length} cảnh báo parse</div>}
                </td>
                <td className="px-2 py-1.5 text-xs">
                  {Object.entries(r.values).map(([col, v]) => (
                    <div key={col}>
                      <span className="text-slate-500">{col}:</span> <span className="font-mono">{fmtValue(v)}</span>
                    </div>
                  ))}
                </td>
                <td className="px-2 py-1.5 text-xs">{r.risk_score}</td>
                <td className="px-2 py-1.5 text-xs">{r.in_sample ? "✓" : ""}</td>
                <td className="px-2 py-1.5">
                  <StatusBadge status={r.review_status} label={STATUS_VI[r.review_status] ?? r.review_status} />
                </td>
                <td className="px-2 py-1.5 text-xs text-slate-600">
                  {r.last_decision ? `${STATUS_VI[r.last_decision.decision]} · ${r.last_decision.reviewer}` : "–"}
                </td>
              </tr>
            ))}
            {data && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-2 py-6 text-center text-sm text-slate-500">
                  Không có dòng khớp bộ lọc.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Drawer
        open={openRow !== null}
        onClose={() => setOpenRow(null)}
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">{openRow}</span>
            {idx >= 0 && (
              <span className="text-xs text-slate-500">
                dòng {idx + 1}/{rows.length}
              </span>
            )}
          </span>
        }
      >
        {openRow && <RowDetail row={rows[idx]} detail={detail} onSaved={() => void load().then(goNext)} />}
      </Drawer>

      {bulk && (
        <BulkApproveModal
          tableCode={tableCode}
          onClose={() => setBulk(false)}
          onDone={() => {
            setBulk(false);
            void load();
          }}
        />
      )}
    </main>
  );
}

function RowDetail({ row, detail, onSaved }: { row?: ReviewRow; detail: ReviewRowDetail | null; onSaved: () => void }) {
  const r = detail ?? row;
  if (!r) return <p className="text-xs text-slate-500">Đang tải…</p>;
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Ô gốc</div>
        <table className="w-full text-xs">
          <tbody>
            {Object.entries(r.cells).map(([c, v]) => (
              <tr key={c} className="border-t border-slate-100 align-top">
                <td className="py-0.5 pr-2 text-slate-500">{c}</td>
                <td className="py-0.5 whitespace-pre-wrap">{v || <span className="text-slate-300">–</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-1 text-[11px] text-slate-500">
          {r.citation_label}
          {r.printed_page && ` · tr. ${r.printed_page}`} · dòng {r.line_start}
          {r.inherits_from && ` · kế thừa ${r.inherits_from}`}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Rule trích xuất ({r.rules.length})</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500">
              <th className="pr-2">Cột</th>
              <th className="pr-2">Giá trị</th>
              <th className="pr-2">Đơn vị</th>
              <th className="pr-2">Thông số</th>
              <th>Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {r.rules.map((rule) => (
              <tr key={rule.rule_code} className="border-t border-slate-100 align-top">
                <td className="pr-2">{rule.zone}</td>
                <td className="pr-2 font-mono">
                  {rule.value != null ? `${rule.op} ${rule.value}` : `${rule.min ?? ""}–${rule.max ?? ""}`}
                  {rule.ref_o2 != null && ` (O₂ ${rule.ref_o2}%)`}
                </td>
                <td className="pr-2">{rule.unit_raw}</td>
                <td className="pr-2">
                  {rule.parameter_id ?? <span className="text-amber-700">chưa map: {rule.parameter_raw}</span>}
                  {rule.equipment_type_id && ` · ${rule.equipment_type_id}`}
                </td>
                <td>{STATUS_VI[rule.review_status] ?? rule.review_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(r.conditions.length > 0 || r.exceptions.length > 0 || r.footnote_refs.length > 0 || r.parse_warnings.length > 0) && (
        <div className="grid gap-2 text-xs sm:grid-cols-2">
          {r.conditions.length > 0 && (
            <div>
              <div className="font-semibold text-slate-500">Điều kiện</div>
              <ul className="list-disc pl-4">
                {r.conditions.map((c, i) => (
                  <li key={i}>
                    {c.type}
                    {c.op ? ` ${c.op}` : ""} {c.value != null ? String(c.value) : ""} {c.unit ?? ""}
                    {c.text ? ` — ${c.text}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.exceptions.length > 0 && (
            <div>
              <div className="font-semibold text-slate-500">Ngoại lệ</div>
              <ul className="list-disc pl-4">
                {r.exceptions.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          {r.footnote_refs.length > 0 && (
            <div>
              <div className="font-semibold text-slate-500">Chú thích</div>
              <ul className="list-disc pl-4">
                {r.footnote_refs.map((f) => (
                  <li key={f}>
                    <b>{f}</b>: {detail?.footnotes?.[f] ?? "…"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.parse_warnings.length > 0 && (
            <div>
              <div className="font-semibold text-amber-700">Cảnh báo parse</div>
              <ul className="list-disc pl-4 text-amber-800">
                {r.parse_warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div>
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Đoạn văn bản (evidence chunk)</div>
        {detail ? (
          detail.evidence ? (
            <pre className="whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 font-sans text-xs leading-relaxed">
              {detail.evidence.context_text || detail.evidence.text}
            </pre>
          ) : (
            <p className="text-xs text-slate-500">Không có chunk gắn với dòng này.</p>
          )
        ) : (
          <p className="text-xs text-slate-500">Đang tải…</p>
        )}
      </div>

      <DecisionForm targetType="table_rows" targetId={r.row_id} currentStatus={r.review_status} onSaved={onSaved} />

      <div>
        <div className="mb-1 text-xs font-semibold uppercase text-slate-500">Lịch sử</div>
        <DecisionHistory items={detail?.decisions ?? []} />
      </div>
    </div>
  );
}
