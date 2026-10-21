import type { EvidencePath, GraphCheck, GraphPathStatus } from "@/lib/api";

/** Rule 8 status of one citation: icon + text, never colour alone. */
export const GRAPH_STATUS_UI: Record<GraphPathStatus, { icon: string; label: string; cls: string }> = {
  connected: { icon: "✓", label: "Nối với câu hỏi", cls: "border-emerald-300 bg-emerald-50 text-emerald-800" },
  extra_sibling: { icon: "!", label: "Nêu thêm giá trị cột/dải khác", cls: "border-amber-300 bg-amber-50 text-amber-900" },
  contradicted: { icon: "✕", label: "Số không khớp điều kiện câu hỏi", cls: "border-rose-300 bg-rose-50 text-rose-800" },
  weak: { icon: "?", label: "Chưa nối được với câu hỏi", cls: "border-amber-300 bg-amber-50 text-amber-900" },
  unchecked: { icon: "–", label: "Chưa kiểm", cls: "border-slate-300 bg-slate-50 text-slate-600" },
};

const NODE_LABEL: Record<string, string> = {
  Parameter: "Thông số",
  LimitRule: "Limit rule",
  TableRow: "Dòng bảng",
  EvidenceChunk: "Căn cứ",
  Zone: "Vùng",
  Structure: "Mục",
  LegalDocument: "Văn bản",
  TransitionRule: "Chuyển tiếp",
  RegistryFact: "Sổ đăng ký",
  IndustrySector: "Ngành",
  EquipmentType: "Thiết bị",
  FuelType: "Nhiên liệu",
  ReceivingBodyType: "Nguồn tiếp nhận",
  Condition: "Điều kiện",
};

export function GraphStatusBadge({ path }: { path: EvidencePath }) {
  const ui = GRAPH_STATUS_UI[path.status] ?? GRAPH_STATUS_UI.unchecked;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${ui.cls}`}
      title={path.reason ?? ui.label}
    >
      <span aria-hidden="true">{ui.icon}</span>
      {ui.label}
    </span>
  );
}

/** The graph path behind a citation (luật 8): node → edge → node, and for table rows the limit-rule values. */
export function GraphPathDetail({ path, mode }: { path: EvidencePath; mode?: GraphCheck["mode"] }) {
  const values = path.allowed_values.length > 0 || path.sibling_values.length > 0;
  return (
    <div className="rounded border border-slate-200 bg-white p-2 text-slate-700">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="font-semibold">Đường căn cứ trên đồ thị</span>
        <GraphStatusBadge path={path} />
        {path.hops != null && <span className="text-slate-500">{path.hops} bước</span>}
        {mode === "observe" && path.status !== "connected" && (
          <span className="text-slate-500">(chỉ ghi nhận, không chặn câu trả lời)</span>
        )}
      </div>
      {path.nodes.length > 0 && (
        <ol className="flex flex-wrap items-center gap-x-1 gap-y-1">
          {path.nodes.map((n, i) => (
            <li key={`${n.id ?? i}-${i}`} className="flex items-center gap-1">
              {i > 0 && (
                <span className="font-mono text-[10px] text-slate-400">
                  –{path.rels[i - 1] ?? ""}→
                </span>
              )}
              <span className="rounded bg-slate-100 px-1.5 py-0.5">
                <span className="text-slate-500">{NODE_LABEL[n.label ?? ""] ?? n.label}: </span>
                {n.name ?? n.id}
              </span>
            </li>
          ))}
        </ol>
      )}
      {values && (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          {path.allowed_values.length > 0 && (
            <>
              <dt className="text-slate-500">Giá trị khớp câu hỏi</dt>
              <dd className="font-mono">{path.allowed_values.join(", ")}</dd>
            </>
          )}
          {path.sibling_values.length > 0 && (
            <>
              <dt className="text-slate-500">Giá trị cột/dải khác</dt>
              <dd className="font-mono">{path.sibling_values.join(", ")}</dd>
            </>
          )}
        </dl>
      )}
      {path.reason && <p className="mt-1 text-slate-600">{path.reason}</p>}
    </div>
  );
}

/** One-line summary of rule 8 under an answer. */
export function GraphCheckSummary({ check, paths }: { check?: GraphCheck | null; paths: EvidencePath[] }) {
  if (!check || check.mode === "off") return null;
  if (check.skipped && paths.length === 0) {
    return <p className="text-xs text-slate-500">Kiểm chứng đồ thị (luật 8): bỏ qua — {check.skipped}</p>;
  }
  const count = (s: GraphPathStatus) => paths.filter((p) => p.status === s).length;
  const bad = count("contradicted");
  const warn = count("extra_sibling") + count("weak");
  return (
    <div
      className={`rounded border px-2 py-1 text-xs ${
        bad > 0 ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-600"
      }`}
    >
      <span className="font-semibold">Kiểm chứng đồ thị (luật 8{check.mode === "observe" ? ", chế độ observe" : ""}): </span>
      {count("connected")} căn cứ nối được với câu hỏi
      {warn > 0 && ` · ${warn} cảnh báo`}
      {bad > 0 && ` · ${bad} số không khớp điều kiện câu hỏi`}
      {bad > 0 && check.mode === "observe" && " — chỉ ghi nhận, không chặn; kiểm tra lại cột/dải trong căn cứ"}
    </div>
  );
}
