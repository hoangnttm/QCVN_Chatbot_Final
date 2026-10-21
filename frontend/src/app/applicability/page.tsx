"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { API_URL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

type Option = { value: string; label: string };
type FieldSpec = {
  name: string;
  type: "bool" | "enum" | "number" | "date" | "list" | "string";
  label: string;
  required_for: string[];
  options?: Option[];
  help?: string | null;
};
type Schema = { domain: string; label: string; fields: FieldSpec[]; rules_version: number };

type Evidence = { chunk_id: string; citation_label: string; document_id: string; text_status: string };
type Result = {
  assessment_id: string;
  domain: string;
  as_of_date: string;
  effective_zone: string | null;
  zone_defaulted: boolean;
  candidates: { document_id: string; document_code: string; status: string; reason: string; pointers: string[] }[];
  transition: { applies: string; explanation: string; effective_date: string | null; effective_source: string | null; deadline: string | null; old_standards: string[] } | null;
  applied_rules: { rule_id: string; title: string; outcome: string; message: string | null; detail: string | null; evidence: Evidence[] }[];
  parameter_set: string[];
  parameter_notes: string[];
  limit_rules: {
    rule_code: string; table_code: string; row_id: string; chunk_id: string; parameter_id: string | null; parameter_raw: string;
    zone: string; op: string; value: number | null; min: number | null; max: number | null; unit_raw: string; ref_o2: number | null;
    conditions: string[]; exceptions: string[]; citation_label: string; review_status: string; selected_by: string[];
  }[];
  missing_information: { field: string; label: string; reason: string; rule_ids: string[] }[];
  warnings: string[];
  notes: string[];
  evidence_path: Record<string, unknown>[];
  disclaimer: string;
};

const DOMAINS: Option[] = [
  { value: "wastewater", label: "Nước thải công nghiệp (QCVN 40:2025)" },
  { value: "emission", label: "Khí thải công nghiệp (QCVN 19:2024)" },
];
const CAND_LABEL: Record<string, string> = { candidate: "Ứng viên", excluded: "Không áp dụng", indirect: "Áp dụng gián tiếp" };
const APPLIES_LABEL: Record<string, string> = {
  new_standard: "Áp dụng quy chuẩn mới",
  old_standard_until_deadline: "Quy chuẩn cũ tới hết lộ trình chuyển tiếp",
  not_yet_effective: "Chưa đến ngày hiệu lực",
  unknown: "Chưa xác định",
};

function fmtLimit(r: Result["limit_rules"][number]) {
  const body = r.op === "between" ? `${r.min} – ${r.max}` : `${r.op} ${r.value}`;
  return `${body} ${r.unit_raw}${r.ref_o2 != null ? ` (O₂ ${r.ref_o2} %)` : ""}`;
}

export default function ApplicabilityPage() {
  const [domain, setDomain] = useState("wastewater");
  const [schema, setSchema] = useState<Schema | null>(null);
  const [ctx, setCtx] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSchema(null);
    setResult(null);
    setCtx({});
    fetch(`${API_URL}/api/v1/applicability/schema/${domain}`)
      .then((r) => r.json())
      .then(setSchema)
      .catch((e) => setError(String(e)));
  }, [domain]);

  const grouped = useMemo(() => {
    if (!result) return {};
    const g: Record<string, Result["limit_rules"]> = {};
    for (const r of result.limit_rules) (g[r.table_code] ??= []).push(r);
    return g;
  }, [result]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/v1/applicability-assessments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain, context: ctx }),
      });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      setResult(await res.json());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  function set(name: string, value: unknown) {
    setCtx((c) => ({ ...c, [name]: value === "" ? null : value }));
  }

  return (
    <main className="mx-auto max-w-6xl p-6">
      <h1 className="text-2xl font-bold">Xác định quy chuẩn có khả năng áp dụng</h1>
      <p className="mb-4 text-sm text-slate-600">
        Đánh giá sơ bộ, hoàn toàn dựa trên rule xác định (không dùng LLM) với trích dẫn cho từng kết luận. Không phải kết luận
        pháp lý; không xuất đạt / không đạt.
      </p>

      <form onSubmit={submit} className="grid gap-4 rounded border border-slate-200 p-4 md:grid-cols-2">
        <label className="md:col-span-2 text-sm">
          <span className="font-medium">Lĩnh vực</span>
          <select className="mt-1 w-full rounded border border-slate-300 p-2" value={domain} onChange={(e) => setDomain(e.target.value)}>
            {DOMAINS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        {schema?.fields.map((f) => (
          <label key={f.name} className="text-sm">
            <span className="font-medium">{f.label}</span>
            {f.required_for.length > 0 && <span className="ml-1 text-[11px] text-slate-400">({f.required_for.join(", ")})</span>}
            {f.type === "bool" ? (
              <select className="mt-1 w-full rounded border border-slate-300 p-2" value={ctx[f.name] === true ? "true" : ctx[f.name] === false ? "false" : ""} onChange={(e) => set(f.name, e.target.value === "" ? null : e.target.value === "true")}>
                <option value="">Không rõ</option>
                <option value="true">Có</option>
                <option value="false">Không</option>
              </select>
            ) : f.type === "enum" ? (
              <select className="mt-1 w-full rounded border border-slate-300 p-2" value={(ctx[f.name] as string) ?? ""} onChange={(e) => set(f.name, e.target.value)}>
                <option value="">— chọn —</option>
                {f.options?.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : f.type === "list" ? (
              <select multiple className="mt-1 h-28 w-full rounded border border-slate-300 p-2" value={(ctx[f.name] as string[]) ?? []} onChange={(e) => set(f.name, Array.from(e.target.selectedOptions).map((o) => o.value))}>
                {f.options?.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type={f.type === "number" ? "number" : f.type === "date" ? "date" : "text"}
                step="any"
                className="mt-1 w-full rounded border border-slate-300 p-2"
                value={(ctx[f.name] as string | number) ?? ""}
                onChange={(e) => set(f.name, f.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
              />
            )}
            {f.help && <span className="block text-[11px] text-slate-500">{f.help}</span>}
          </label>
        ))}
        <div className="md:col-span-2">
          <button type="submit" disabled={busy || !schema} className="rounded bg-sky-700 px-4 py-2 text-white disabled:opacity-50">
            {busy ? "Đang đánh giá…" : "Đánh giá sơ bộ"}
          </button>
          {error && <span className="ml-3 text-sm text-rose-700">{error}</span>}
        </div>
      </form>

      {result && (
        <section className="mt-6 space-y-4">
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">{result.disclaimer}</div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded border border-slate-200 p-4">
              <h2 className="font-semibold">Quy chuẩn ứng viên</h2>
              <ul className="mt-2 space-y-2 text-sm">
                {result.candidates.length === 0 && <li className="text-slate-500">Chưa đủ thông tin để xác định.</li>}
                {result.candidates.map((c) => (
                  <li key={c.document_id}>
                    <Link href={`/documents/${encodeURIComponent(c.document_id)}`} className="font-medium text-sky-700 hover:underline">
                      {c.document_code}
                    </Link>{" "}
                    <StatusBadge status={c.status === "candidate" ? "approved" : c.status === "excluded" ? "rejected" : "needs_recheck"} label={CAND_LABEL[c.status] ?? c.status} />
                    <div className="text-slate-700">{c.reason}</div>
                    {c.pointers.map((p) => (
                      <div key={p} className="text-[11px] text-slate-500">→ {p}</div>
                    ))}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-sm">
                Cột áp dụng: <strong>{result.effective_zone ?? "chưa xác định"}</strong>
                {result.zone_defaulted && <span className="text-amber-700"> (mặc định Cột B)</span>} · thời điểm {result.as_of_date}
              </p>
              {result.transition && (
                <p className="mt-2 text-sm">
                  <span className="rounded bg-slate-100 px-1.5 text-xs">{APPLIES_LABEL[result.transition.applies] ?? result.transition.applies}</span>{" "}
                  {result.transition.explanation}
                </p>
              )}
            </div>
            <div className="rounded border border-slate-200 p-4">
              <h2 className="font-semibold">Cảnh báo / còn thiếu</h2>
              <ul className="mt-2 list-disc pl-5 text-sm text-amber-800">
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
              {result.missing_information.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm text-rose-800">
                  {result.missing_information.map((mi) => (
                    <li key={mi.field}>
                      <strong>{mi.label}</strong>: {mi.reason} <span className="text-[11px] text-slate-500">({mi.rule_ids.join(", ")})</span>
                    </li>
                  ))}
                </ul>
              )}
              {result.notes.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-sm text-slate-700">
                  {result.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {result.parameter_set.length > 0 && (
            <div className="rounded border border-slate-200 p-4 text-sm">
              <h2 className="font-semibold">Thông số phải kiểm soát</h2>
              <div className="mt-1 flex flex-wrap gap-1">
                {result.parameter_set.map((p) => (
                  <span key={p} className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs">
                    {p}
                  </span>
                ))}
              </div>
              {result.parameter_notes.map((n, i) => (
                <p key={i} className="mt-1 text-slate-600">
                  {n}
                </p>
              ))}
            </div>
          )}

          {Object.entries(grouped).map(([table, rows]) => (
            <div key={table} className="rounded border border-slate-200 p-4">
              <h2 className="font-semibold">
                Giới hạn áp dụng — {table} ({rows.length})
              </h2>
              <div className="overflow-x-auto">
                <table className="mt-2 min-w-full text-sm">
                  <thead className="text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th className="px-2 py-1">Thông số</th>
                      <th className="px-2 py-1">Cột</th>
                      <th className="px-2 py-1">Giới hạn</th>
                      <th className="px-2 py-1">Điều kiện / ngoại lệ</th>
                      <th className="px-2 py-1">Trích dẫn</th>
                      <th className="px-2 py-1">Duyệt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.rule_code} className="border-t border-slate-100 align-top">
                        <td className="px-2 py-1">{r.parameter_raw}</td>
                        <td className="px-2 py-1">{r.zone}</td>
                        <td className="whitespace-nowrap px-2 py-1 font-mono">{fmtLimit(r)}</td>
                        <td className="px-2 py-1 text-xs text-slate-600">
                          {r.conditions.join("; ")}
                          {r.exceptions.length > 0 && <div className="text-rose-700">ngoại lệ: {r.exceptions.join("; ")}</div>}
                          <div className="text-slate-400">{r.selected_by.join(", ")}</div>
                        </td>
                        <td className="px-2 py-1 text-xs">
                          <Link href={`/documents/${encodeURIComponent(r.table_code.startsWith("QCVN40") ? "QCVN_40_2025" : "QCVN_19_2024")}/tables/${encodeURIComponent(r.table_code)}#${r.row_id}`} className="text-sky-700 hover:underline">
                            {r.citation_label}
                          </Link>
                        </td>
                        <td className="px-2 py-1">
                          <StatusBadge status={r.review_status} label={r.review_status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          <div className="rounded border border-slate-200 p-4">
            <h2 className="font-semibold">Rule đã xét và căn cứ</h2>
            <ul className="mt-2 space-y-2 text-sm">
              {result.applied_rules.map((ar) => (
                <li key={ar.rule_id}>
                  <span className="font-mono text-xs">{ar.rule_id}</span>{" "}
                  <StatusBadge status={ar.outcome === "applied" ? "approved" : ar.outcome === "skipped_missing" ? "needs_recheck" : "pending"} label={ar.outcome === "applied" ? "áp dụng" : ar.outcome === "skipped_missing" ? "thiếu thông tin" : "không kích hoạt"} />{" "}
                  <span>{ar.title}</span>
                  {ar.detail && <div className="text-xs text-slate-600">{ar.detail}</div>}
                  {ar.evidence.length > 0 && (
                    <div className="text-[11px] text-slate-500">
                      {ar.evidence.map((ev) => (
                        <span key={ev.chunk_id} className="mr-2">
                          {ev.citation_label}
                          {ev.text_status === "draft" && <span className="text-amber-700"> (dự thảo)</span>}
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </main>
  );
}
