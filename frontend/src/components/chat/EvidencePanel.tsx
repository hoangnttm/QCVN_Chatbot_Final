"use client";

import Link from "next/link";
import { StatusBadge } from "@/components/StatusBadge";
import { GraphPathDetail, GraphStatusBadge } from "@/components/chat/GraphPath";
import type { EvidenceItem, EvidencePath, GraphCheck, PathNode, StructuredHit } from "@/lib/api";

export const CHUNK_TYPE_LABEL: Record<string, string> = {
  table_row: "Dòng bảng",
  clause: "Khoản",
  article: "Điều",
  point: "Điểm",
  paragraph: "Đoạn",
  footnote: "Chú thích",
  definition: "Định nghĩa",
  registry_fact: "Sổ đăng ký",
  section: "Mục",
  preamble: "Lời nói đầu",
  monitoring_requirement: "Yêu cầu quan trắc",
};

export type PathState = Record<string, PathNode[] | "loading" | "error">;

export function EvidencePanel({
  turnId,
  evidence,
  structured = [],
  cited,
  selectedChunk,
  paths,
  onSelect,
  compact = false,
  graphPaths = [],
  graphMode,
}: {
  turnId: string | number;
  evidence: EvidenceItem[];
  structured?: StructuredHit[];
  cited: Set<string>;
  selectedChunk: string | null;
  paths: PathState;
  onSelect: (chunk: string) => void;
  compact?: boolean;
  /** rule 8 result per cited chunk (validator graph check) */
  graphPaths?: EvidencePath[];
  graphMode?: GraphCheck["mode"];
}) {
  const graphOf = new Map(graphPaths.map((g) => [g.chunk_id, g]));
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold">
        Căn cứ ({evidence.length}){structured.length > 0 && ` · ${structured.length} dòng ngưỡng khớp`}
      </h3>
      {structured.length > 0 && !compact && (
        <div className="mb-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-slate-500">
                <th className="pr-2">Thông số</th>
                <th className="pr-2">Cột</th>
                <th className="pr-2">Giá trị</th>
                <th>Điều kiện</th>
              </tr>
            </thead>
            <tbody>
              {structured.map((h) => (
                <tr key={h.rule_code} className="border-t border-slate-100 align-top">
                  <td className="pr-2">{h.parameter_id ?? h.parameter_raw}</td>
                  <td className="pr-2">{h.zone}</td>
                  <td className="pr-2 font-mono">
                    {h.value != null ? `${h.op} ${h.value}` : `${h.min ?? ""}–${h.max ?? ""}`} {h.unit_raw}
                    {h.ref_o2 != null && ` (O₂ ${h.ref_o2}%)`}
                  </td>
                  <td className="text-slate-600">{h.conditions.join("; ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ol className="space-y-2">
        {evidence.map((e, i) => {
          const open = selectedChunk === e.chunk_id;
          const path = paths[e.chunk_id];
          const gp = graphOf.get(e.chunk_id);
          return (
            <li
              key={e.chunk_id}
              id={`ev-${turnId}-${e.chunk_id}`}
              className={`rounded border p-2 text-xs ${
                open ? "border-sky-400 bg-sky-50" : cited.has(e.chunk_id) ? "border-emerald-300" : "border-slate-200"
              }`}
            >
              <button type="button" className="w-full text-left" onClick={() => onSelect(e.chunk_id)}>
                <div className="flex flex-wrap items-center gap-1">
                  <span className="font-semibold text-sky-800">[{i + 1}]</span>
                  <span className="font-medium">{e.citation_label}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1 text-slate-500">
                  <StatusBadge status={e.text_status} />
                  <span>{CHUNK_TYPE_LABEL[e.chunk_type] ?? e.chunk_type}</span>
                  {e.sources?.length > 0 && <span>· {e.sources.join(", ")}</span>}
                  {cited.has(e.chunk_id) && <span className="text-emerald-700">· được trích</span>}
                </div>
                {gp && (
                  <div className="mt-1">
                    <GraphStatusBadge path={gp} />
                  </div>
                )}
              </button>
              {open && (
                <div className="mt-2 space-y-2">
                  <pre className="whitespace-pre-wrap rounded bg-white p-2 font-sans text-xs leading-relaxed text-slate-800">
                    {e.text}
                  </pre>
                  <div className="text-slate-600">
                    <span className="font-semibold">Vị trí: </span>
                    {path === "loading" && "đang tải…"}
                    {path === "error" && "không lấy được đường dẫn"}
                    {Array.isArray(path) &&
                      path.map((n, j) => (
                        <span key={n.id}>
                          {j > 0 && " › "}
                          {n.kind}
                          {n.number ? ` ${n.number}` : ""}
                          {n.title ? ` — ${n.title}` : ""}
                          {n.printed_page ? ` (tr. ${n.printed_page})` : ""}
                        </span>
                      ))}
                  </div>
                  {gp && <GraphPathDetail path={gp} mode={graphMode} />}
                  <Link
                    href={`/documents/${encodeURIComponent(e.document_id)}`}
                    className="text-sky-700 hover:underline"
                    onClick={(ev) => ev.stopPropagation()}
                  >
                    Mở văn bản {e.document_code}
                  </Link>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
