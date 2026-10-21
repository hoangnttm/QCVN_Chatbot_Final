"use client";

import type { HistoryItem } from "@/lib/api";

const STATUS_DOT: Record<string, string> = {
  answered: "bg-emerald-500",
  abstained: "bg-amber-500",
  failed: "bg-rose-500",
  error: "bg-rose-500",
};

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (same(d, today)) return "Hôm nay";
  const y = new Date(today);
  y.setDate(today.getDate() - 1);
  if (same(d, y)) return "Hôm qua";
  return d.toLocaleDateString("vi-VN");
}

export function HistorySidebar({
  items,
  loading,
  activeId,
  onOpen,
  onRefresh,
}: {
  items: HistoryItem[];
  loading: boolean;
  activeId: string | null;
  onOpen: (item: HistoryItem) => void;
  onRefresh: () => void;
}) {
  const groups = new Map<string, HistoryItem[]>();
  for (const it of items) {
    const k = dayLabel(it.created_at);
    groups.set(k, [...(groups.get(k) ?? []), it]);
  }
  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Lịch sử</h3>
        <button type="button" onClick={onRefresh} className="text-xs text-sky-700 hover:underline">
          Tải lại
        </button>
      </div>
      <p className="mb-2 text-[11px] text-slate-500">Lưu ẩn danh theo trình duyệt này; đổi máy sẽ không thấy.</p>
      {loading && <p className="text-xs text-slate-500">Đang tải…</p>}
      {!loading && items.length === 0 && <p className="text-xs text-slate-500">Chưa có câu hỏi nào.</p>}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {[...groups.entries()].map(([day, list]) => (
          <div key={day}>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{day}</div>
            <ul className="space-y-1">
              {list.map((it) => (
                <li key={it.query_id}>
                  <button
                    type="button"
                    onClick={() => onOpen(it)}
                    className={`flex w-full items-start gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-slate-100 ${
                      activeId === it.query_id ? "bg-sky-50" : ""
                    }`}
                  >
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[it.status] ?? "bg-slate-400"}`} />
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-slate-800">{it.question}</span>
                      {it.conclusion && <span className="line-clamp-1 text-slate-500">{it.conclusion}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
