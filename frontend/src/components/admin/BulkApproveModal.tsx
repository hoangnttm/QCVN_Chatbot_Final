"use client";

import { useEffect, useState } from "react";
import { admin, REVIEWER_KEY, type ReviewTable } from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { showToast } from "@/components/ui/Toast";

type Blocked = { code: string; rows_total: number; required: number; reviewed_rows: number; blocking_rows: { row_id: string; status: string; reviewer: string; note: string | null }[] };

export function useReviewer(): [string, (v: string) => void] {
  const [name, setName] = useState("");
  useEffect(() => {
    try {
      setName(window.localStorage.getItem(REVIEWER_KEY) ?? "");
    } catch {
      /* ignore */
    }
  }, []);
  const set = (v: string) => {
    setName(v);
    try {
      window.localStorage.setItem(REVIEWER_KEY, v);
    } catch {
      /* ignore */
    }
  };
  return [name, set];
}

/** Confirmation for "approve whole table": shows the 10 % sample check first, then posts; 409 lists blocking rows. */
export function BulkApproveModal({ tableCode, onClose, onDone }: { tableCode: string; onClose: () => void; onDone: () => void }) {
  const [check, setCheck] = useState<ReviewTable["sample"] | null>(null);
  const [rulesPending, setRulesPending] = useState<number | null>(null);
  const [reviewer, setReviewer] = useReviewer();
  const [note, setNote] = useState("Đã duyệt mẫu ≥ 10 % dòng rủi ro cao, không phát hiện sai lệch.");
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState<Blocked | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    admin
      .reviewTable(tableCode, new URLSearchParams({ in_sample: "true" }))
      .then((t) => {
        setCheck(t.sample);
        setRulesPending(t.rules_by_status.pending ?? 0);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [tableCode]);

  async function submit() {
    if (!reviewer.trim() || !note.trim()) return;
    setBusy(true);
    setError(null);
    setBlocked(null);
    try {
      const r = await admin.bulkApprove(tableCode, { reviewer: reviewer.trim(), note: note.trim() });
      showToast(`Đã duyệt ${r.approved_now} rule của ${r.table}`, "success", 5000);
      onDone();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.detailObject && typeof e.detailObject === "object") {
        setBlocked(e.detailObject as Blocked);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={`Duyệt cả bảng ${tableCode}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
            Hủy
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy || !reviewer.trim() || !note.trim() || !check?.ok || (rulesPending ?? 0) === 0}
            className="rounded bg-sky-700 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Đang duyệt…" : `Xác nhận duyệt ${rulesPending ?? ""} rule chờ`}
          </button>
        </>
      }
    >
      <p className="mb-3 text-slate-700">
        Mọi rule còn trạng thái <b>chờ</b> trong bảng sẽ chuyển thành <b>đã duyệt</b>, ghi một quyết định
        <code className="mx-1">limit_rules:table</code>và một sự kiện audit. Điều kiện (PLAN.md §11): tất cả dòng trong mẫu 10 %
        phải được duyệt &quot;Đúng&quot;, không có dòng bị từ chối/cần làm rõ.
      </p>
      {check ? (
        <div className={`mb-3 rounded border p-2 text-xs ${check.ok ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`}>
          <div>
            Mẫu: {check.reviewed_rows} / {check.required} dòng đã duyệt Đúng (trong {check.n} dòng mẫu) →{" "}
            {check.ok ? "đủ điều kiện" : "chưa đủ điều kiện"}
          </div>
          {check.blocking_rows.length > 0 && (
            <ul className="mt-1 list-disc pl-4 text-rose-800">
              {check.blocking_rows.map((b) => (
                <li key={b.row_id}>
                  {b.row_id} — {b.status} ({b.reviewer}){b.note ? `: ${b.note}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <p className="mb-3 text-xs text-slate-500">Đang kiểm tra mẫu…</p>
      )}
      <label className="mb-2 block text-xs">
        <span className="text-slate-500">Người duyệt</span>
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1" placeholder="Tên chuyên gia" />
      </label>
      <label className="block text-xs">
        <span className="text-slate-500">Lý do / ghi chú (bắt buộc)</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1" />
      </label>
      {blocked && (
        <div className="mt-3 rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-900">
          <div className="font-semibold">Máy chủ từ chối: mẫu chưa đủ ({blocked.reviewed_rows} / {blocked.required}).</div>
          {blocked.blocking_rows.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {blocked.blocking_rows.map((b) => (
                <li key={b.row_id}>
                  {b.row_id} — {b.status} ({b.reviewer}){b.note ? `: ${b.note}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <p className="mt-3 text-xs text-rose-700">{error}</p>}
    </Modal>
  );
}
