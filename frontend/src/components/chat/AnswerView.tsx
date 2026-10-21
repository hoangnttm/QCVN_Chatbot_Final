import { StatusBadge } from "@/components/StatusBadge";
import { GraphCheckSummary } from "@/components/chat/GraphPath";
import { graphPathsOf, type Answer, type Citation, type EvidenceItem, type EvidencePath, type GraphCheck } from "@/lib/api";

export type AnswerLike = {
  answer: Answer;
  citations: Citation[];
  evidence: EvidenceItem[];
  status: "answered" | "abstained" | "failed" | "error";
  validation?: { ok?: boolean; errors?: string[]; graph?: GraphCheck | null } | Record<string, never>;
  evidence_paths?: EvidencePath[];
  path?: string;
  model?: string | null;
  thinking_level?: string | null;
  attempts?: number;
  latency_ms?: Record<string, number>;
  usage?: Record<string, number>;
  cost_usd?: number | null;
  disclaimer?: string;
};

export const STATUS_UI: Record<AnswerLike["status"], { badge: string; label: string }> = {
  answered: { badge: "approved", label: "Có căn cứ" },
  abstained: { badge: "needs_recheck", label: "Không kết luận" },
  failed: { badge: "rejected", label: "Từ chối" },
  error: { badge: "rejected", label: "Lỗi hệ thống" },
};

/** Renders conclusion / basis / conditions / warnings with clickable [n] citation chips. */
export function AnswerView({
  r,
  extraWarnings = [],
  onCite,
  footer,
}: {
  r: AnswerLike;
  extraWarnings?: string[];
  onCite?: (chunkId: string) => void;
  footer?: React.ReactNode;
}) {
  const index = new Map(r.evidence.map((e, i) => [e.chunk_id, i + 1]));
  const chip = (chunk: string, key: string) => (
    <button
      key={key}
      type="button"
      onClick={() => onCite?.(chunk)}
      title={r.evidence.find((e) => e.chunk_id === chunk)?.citation_label ?? chunk}
      className="mx-0.5 inline-flex min-h-7 min-w-7 items-center justify-center rounded bg-sky-100 px-1.5 text-xs font-semibold text-sky-800 hover:bg-sky-200"
    >
      [{index.get(chunk) ?? "?"}]
    </button>
  );
  const a = r.answer;
  const ui = STATUS_UI[r.status] ?? STATUS_UI.failed;
  const warnings = [...new Set([...a.warnings, ...extraWarnings])];
  const validationErrors = (r.validation as { errors?: string[] })?.errors ?? [];
  const validationOk = (r.validation as { ok?: boolean })?.ok;
  return (
    <div className="space-y-3 text-sm">
      <div
        className={`rounded p-3 ${
          a.abstained ? "border border-amber-300 bg-amber-50" : "border border-emerald-200 bg-emerald-50"
        }`}
      >
        <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge status={ui.badge} label={ui.label} />
          {a.abstain_reason && <span className="text-slate-600">{a.abstain_reason}</span>}
        </div>
        <p className="whitespace-pre-wrap text-base leading-relaxed">
          {a.conclusion}
          {a.citations.map((c) => chip(c, `c-${c}`))}
        </p>
      </div>

      {a.basis.length > 0 && (
        <div>
          <h4 className="mb-1 font-semibold">Căn cứ</h4>
          <ul className="list-disc space-y-1 pl-5">
            {a.basis.map((b, i) => (
              <li key={i}>
                {b.text}
                {b.citations.map((c) => chip(c, `b-${i}-${c}`))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.conditions.length > 0 && (
        <div>
          <h4 className="mb-1 font-semibold">Điều kiện áp dụng</h4>
          <ul className="list-disc pl-5">
            {a.conditions.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {a.missing_information.length > 0 && (
        <div>
          <h4 className="mb-1 font-semibold">Cần thêm thông tin</h4>
          <ul className="list-disc pl-5 text-slate-700">
            {a.missing_information.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="space-y-1">
          {warnings.map((w, i) => (
            <li key={i} className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}
      {validationOk === false && validationErrors.length > 0 && (
        <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-800">
          Bộ kiểm tra trích dẫn từ chối câu trả lời của mô hình: {validationErrors.join("; ")}
        </div>
      )}
      <GraphCheckSummary
        check={(r.validation as { graph?: GraphCheck | null } | undefined)?.graph}
        paths={graphPathsOf(r)}
      />
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-2 text-xs text-slate-500">
        <span>
          {r.path === "policy"
            ? "Trả lời theo chính sách (không dùng LLM)"
            : `${r.model ?? "LLM"} · thinking ${r.thinking_level ?? "-"}`}
          {(r.attempts ?? 0) > 1 && ` · sinh lại ${(r.attempts ?? 1) - 1} lần`}
        </span>
        {r.latency_ms?.total != null && <span>{r.latency_ms.total} ms</span>}
        {r.usage?.total_tokens != null && <span>{r.usage.total_tokens} token</span>}
        {r.cost_usd != null && r.cost_usd > 0 && <span>{r.cost_usd.toFixed(4)} USD</span>}
        {footer}
      </div>
      {r.disclaimer && <p className="text-xs italic text-slate-500">{r.disclaimer}</p>}
    </div>
  );
}
