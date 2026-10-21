"use client";

import { useState } from "react";
import { admin, type ReviewDecision } from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import { useReviewer } from "@/components/admin/BulkApproveModal";
import { STATUS_VI } from "@/components/admin/ReviewBits";
import { when } from "@/components/ui/Kpi";

/** Approve / reject / needs-recheck form for one review target; note required for the last two. */
export function DecisionForm({
  targetType,
  targetId,
  currentStatus,
  onSaved,
}: {
  targetType: string;
  targetId: string;
  currentStatus: string;
  onSaved: (status: string) => void;
}) {
  const [reviewer, setReviewer] = useReviewer();
  const [note, setNote] = useState("");
  const [correction, setCorrection] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approved" | "rejected" | "needs_recheck") {
    setError(null);
    if (!reviewer.trim()) {
      setError("Nhập tên người duyệt.");
      return;
    }
    if (decision !== "approved" && !note.trim()) {
      setError("Cần ghi chú lý do khi từ chối hoặc yêu cầu làm rõ.");
      return;
    }
    setBusy(decision);
    try {
      const r = await admin.decide({
        target_type: targetType,
        target_id: targetId,
        decision,
        reviewer: reviewer.trim(),
        note: note.trim() || undefined,
        correction: correction.trim() || undefined,
      });
      showToast(`Đã ghi: ${STATUS_VI[r.review_status] ?? r.review_status} (${r.rules_updated} rule)`, "success");
      setNote("");
      setCorrection("");
      onSaved(r.review_status);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">Quyết định</span>
        <span className="text-slate-500">
          hiện tại: {STATUS_VI[currentStatus] ?? currentStatus}
        </span>
      </div>
      <label className="block">
        <span className="text-slate-500">Người duyệt</span>
        <input
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="Tên chuyên gia"
          className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1"
        />
      </label>
      <label className="block">
        <span className="text-slate-500">Sửa thành (nếu dữ liệu trích xuất sai)</span>
        <input
          value={correction}
          onChange={(e) => setCorrection(e.target.value)}
          placeholder="ví dụ: 0,05 mg/L, cột A"
          className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1"
        />
      </label>
      <label className="block">
        <span className="text-slate-500">Ghi chú (bắt buộc khi Từ chối / Cần làm rõ)</span>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1"
        />
      </label>
      {error && <p className="text-rose-700">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("approved")}
          className="rounded bg-emerald-600 px-3 py-1.5 font-semibold text-white disabled:opacity-50"
        >
          {busy === "approved" ? "…" : "Đúng (duyệt)"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("rejected")}
          className="rounded bg-rose-600 px-3 py-1.5 font-semibold text-white disabled:opacity-50"
        >
          {busy === "rejected" ? "…" : "Sai (từ chối)"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => decide("needs_recheck")}
          className="rounded bg-amber-500 px-3 py-1.5 font-semibold text-white disabled:opacity-50"
        >
          {busy === "needs_recheck" ? "…" : "Cần làm rõ"}
        </button>
      </div>
    </div>
  );
}

export function DecisionHistory({ items }: { items: ReviewDecision[] }) {
  if (!items.length) return <p className="text-xs text-slate-500">Chưa có quyết định nào.</p>;
  return (
    <ul className="space-y-1 text-xs">
      {items.map((d) => (
        <li key={d.id} className="border-t border-slate-100 pt-1">
          <span className="font-medium">{STATUS_VI[d.decision] ?? d.decision}</span> · {d.reviewer} · {when(d.reviewed_at)}
          {d.source && <span className="text-slate-500"> · {d.source}</span>}
          {d.correction && <div>Sửa thành: {d.correction}</div>}
          {d.note && <div className="text-slate-600">{d.note}</div>}
        </li>
      ))}
    </ul>
  );
}
