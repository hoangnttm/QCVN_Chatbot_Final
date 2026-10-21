import Link from "next/link";
import type { TreeNode } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  Document: "Văn bản",
  Chapter: "Chương",
  Article: "Điều",
  Section: "Mục",
  Clause: "Khoản",
  Point: "Điểm",
  Appendix: "Phụ lục",
  AppendixSection: "Mục",
  AppendixTable: "Bảng",
  Table: "Bảng",
  Definition: "Định nghĩa",
  Footnote: "Chú thích",
  Preamble: "Lời nói đầu",
  Paragraph: "Đoạn",
  Formula: "Công thức",
  Heading: "Tiêu đề",
  Title: "Tên văn bản",
};

const CONTAINER_KINDS = new Set([
  "Document",
  "Chapter",
  "Article",
  "Section",
  "Appendix",
  "AppendixSection",
  "Table",
  "AppendixTable",
  "Heading",
]);

function NodeLine({ node, docId }: { node: TreeNode; docId: string }) {
  const kind = KIND_LABEL[node.kind] ?? node.kind;
  const head = [kind, node.number ?? node.label].filter(Boolean).join(" ");
  const where = node.span.printed_page ? `tr. ${node.span.printed_page}` : `dòng ${node.span.line_start}`;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <span className="font-semibold text-slate-800">{head}</span>
      {node.title && <span className="text-slate-900">{node.title}</span>}
      {node.term && <span className="italic text-slate-900">{node.term}</span>}
      {node.table_code && (
        <Link
          href={`/documents/${encodeURIComponent(docId)}/tables/${encodeURIComponent(node.table_code)}`}
          className="rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-800 hover:bg-sky-200"
        >
          xem bảng {node.table_code}
        </Link>
      )}
      {node.scope === "structure_only" && (
        <span className="rounded bg-slate-200 px-1.5 text-[11px] text-slate-600">ngoài phạm vi MVP</span>
      )}
      <span className="text-[11px] text-slate-400">{where}</span>
    </div>
  );
}

export function StructureTree({ nodes, docId, depth = 0 }: { nodes: TreeNode[]; docId: string; depth?: number }) {
  return (
    <ul className={depth ? "ml-4 border-l border-slate-200 pl-3" : ""}>
      {nodes.map((n) => {
        const container = CONTAINER_KINDS.has(n.kind) && n.children.length > 0;
        if (container) {
          return (
            <li key={n.node_id} className="my-1">
              <details open={depth < 1}>
                <summary className="cursor-pointer select-none">
                  <NodeLine node={n} docId={docId} />
                </summary>
                {n.text && <p className="ml-4 whitespace-pre-wrap text-sm text-slate-700">{n.text}</p>}
                <StructureTree nodes={n.children} docId={docId} depth={depth + 1} />
              </details>
            </li>
          );
        }
        return (
          <li key={n.node_id} className="my-1" id={n.node_id}>
            <NodeLine node={n} docId={docId} />
            {n.text && <p className="ml-1 whitespace-pre-wrap text-sm text-slate-700">{n.text}</p>}
            {n.children.length > 0 && <StructureTree nodes={n.children} docId={docId} depth={depth + 1} />}
          </li>
        );
      })}
    </ul>
  );
}
