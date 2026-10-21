"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { admin, type ReviewItem } from "@/lib/admin";
import { StatusBadge } from "@/components/StatusBadge";
import { Drawer } from "@/components/ui/Drawer";
import { DecisionForm } from "@/components/admin/DecisionForm";
import { STATUS_VI } from "@/components/admin/ReviewBits";
import { when } from "@/components/ui/Kpi";

const TITLE: Record<string, string> = {
  transition_rules: "Quy tắc chuyển tiếp",
  document_relations: "Quan hệ văn bản rủi ro cao (REPLACES / REPEALS)",
  monitoring_requirements: "Yêu cầu quan trắc",
};

export default function ReviewItemsPage() {
  const { type } = useParams<{ type: string }>();
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await admin.reviewItems(type, status || undefined));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [type, status]);
  useEffect(() => {
    void load();
  }, [load]);

  const list = items ?? [];
  const idx = list.findIndex((i) => i.target_id === open);
  const cur = idx >= 0 ? list[idx] : null;

  if (!TITLE[type]) {
    return <p className="text-sm text-rose-700">Loại đối tượng không hợp lệ: {type}</p>;
  }

  return (
    <main className="space-y-4">
      <Link href="/admin/review" className="text-sm text-sky-700 hover:underline">
        ← Kiểm duyệt
      </Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{TITLE[type]}</h1>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-slate-300 px-2 py-1 text-sm">
          <option value="">Tất cả trạng thái</option>
          <option value="pending">chờ</option>
          <option value="approved">đã duyệt</option>
          <option value="rejected">từ chối</option>
          <option value="needs_recheck">cần làm rõ</option>
        </select>
        <span className="text-sm text-slate-500">{list.length} mục</span>
      </div>
      {error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>}
      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="px-2 py-2">Văn bản</th>
              <th className="px-2 py-2">Loại</th>
              <th className="px-2 py-2">Nội dung</th>
              <th className="px-2 py-2">Trạng thái</th>
              <th className="px-2 py-2">Quyết định gần nhất</th>
            </tr>
          </thead>
          <tbody>
            {list.map((it) => (
              <tr
                key={it.target_id}
                onClick={() => setOpen(it.target_id)}
                className={`cursor-pointer border-t border-slate-100 align-top hover:bg-sky-50 ${open === it.target_id ? "bg-sky-50" : ""}`}
              >
                <td className="whitespace-nowrap px-2 py-1.5 text-xs">{it.document_id}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-xs">{it.kind}</td>
                <td className="px-2 py-1.5">
                  <div>{(it.summary ?? it.text).slice(0, 180)}{(it.summary ?? it.text).length > 180 ? "…" : ""}</div>
                  {it.citation_label && <div className="text-xs text-slate-500">{it.citation_label}</div>}
                </td>
                <td className="px-2 py-1.5">
                  <StatusBadge status={it.review_status} label={STATUS_VI[it.review_status] ?? it.review_status} />
                </td>
                <td className="px-2 py-1.5 text-xs text-slate-600">
                  {it.last_decision ? `${STATUS_VI[it.last_decision.decision]} · ${it.last_decision.reviewer} · ${when(it.last_decision.reviewed_at)}` : "–"}
                </td>
              </tr>
            ))}
            {items && list.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2 py-6 text-center text-sm text-slate-500">
                  Không có mục nào.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Drawer open={cur !== null} onClose={() => setOpen(null)} title={<span className="font-mono text-xs">{cur?.target_id}</span>}>
        {cur && (
          <div className="space-y-3">
            <div className="text-xs text-slate-500">
              {cur.document_id} · {cur.kind}
              {cur.citation_label && ` · ${cur.citation_label}`}
            </div>
            {cur.summary && <p className="text-sm font-medium">{cur.summary}</p>}
            <pre className="whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 font-sans text-xs leading-relaxed">{cur.text}</pre>
            {Object.keys(cur.meta).length > 0 && (
              <pre className="overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600">{JSON.stringify(cur.meta, null, 2)}</pre>
            )}
            {cur.evidence_chunk_id && <div className="text-[11px] text-slate-500">chunk: {cur.evidence_chunk_id}</div>}
            <DecisionForm
              targetType={cur.target_type}
              targetId={cur.target_id}
              currentStatus={cur.review_status}
              onSaved={() => {
                void load().then(() => {
                  if (idx + 1 < list.length) setOpen(list[idx + 1].target_id);
                  else setOpen(null);
                });
              }}
            />
            {cur.last_decision && (
              <div className="text-xs text-slate-600">
                Gần nhất: {STATUS_VI[cur.last_decision.decision]} · {cur.last_decision.reviewer} · {when(cur.last_decision.reviewed_at)}
                {cur.last_decision.note && <div>{cur.last_decision.note}</div>}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </main>
  );
}
