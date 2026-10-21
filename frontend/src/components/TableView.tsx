import type { TableDetail, TableRow, Value } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

const COLUMN_LABEL: Record<string, string> = {
  tt: "TT",
  stt: "TT",
  parameter: "Thông số",
  unit: "Đơn vị",
  equipment: "Thiết bị / nhóm",
  item: "Nội dung",
  methods: "Phương pháp",
  field_methods: "Phương pháp hiện trường",
  lab_methods: "Phương pháp phòng thí nghiệm",
  sector: "Loại hình",
  vsic_codes: "Mã ngành",
  characteristic_parameters: "Thông số đặc trưng",
  other_parameters: "Thông số khác",
  congener: "Chất",
  pah: "Chất",
  tef: "TEF",
};

function fmtValue(v: Value | undefined) {
  if (!v) return "";
  const body = v.op === "between" ? `${v.min} – ${v.max}` : `${v.op} ${v.value}`;
  return v.ref_o2 != null ? `${body} (O₂ ${v.ref_o2} %)` : body;
}

function isValueColumn(t: TableDetail, col: string) {
  return t.rows.some((r) => r.values[col] !== undefined);
}

function RowNotes({ r }: { r: TableRow }) {
  const conds = r.conditions.filter((c) => c.type !== "zone" && c.type !== "equipment_path");
  if (!conds.length && !r.exceptions.length && !r.parse_warnings.length) return null;
  return (
    <div className="mt-1 space-y-0.5 text-[11px] text-slate-600">
      {conds.map((c, i) => (
        <div key={i}>
          <span className="rounded bg-slate-100 px-1">{c.type}</span> {c.text ?? `${c.op ?? ""} ${String(c.value)}`}
          {c.inherited_from && <span className="text-slate-400"> (kế thừa từ dòng {c.inherited_from})</span>}
        </div>
      ))}
      {r.exceptions.map((e, i) => (
        <div key={`e${i}`} className="text-rose-700">
          ngoại lệ: {e}
        </div>
      ))}
      {r.parse_warnings.map((w, i) => (
        <div key={`w${i}`} className="text-amber-700">
          ⚠ {w}
        </div>
      ))}
    </div>
  );
}

export function TableView({ table }: { table: TableDetail }) {
  const cols = table.columns;
  const footnotes = Object.entries(table.footnotes).filter(([, t]) => t);
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
            <tr>
              {cols.map((c) => (
                <th key={c} className="px-2 py-2 align-bottom">
                  {COLUMN_LABEL[c] ?? c}
                </th>
              ))}
              <th className="px-2 py-2">Duyệt</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r) => (
              <tr
                key={r.row_id}
                id={r.row_id}
                className={r.is_group_header ? "bg-slate-100 font-semibold" : "border-t border-slate-100 align-top"}
              >
                {cols.map((c) => (
                  <td key={c} className="px-2 py-1.5" style={{ paddingLeft: c === cols[1] ? `${8 + (r.row_path.length - 1) * 12}px` : undefined }}>
                    {isValueColumn(table, c) ? (
                      <span className="whitespace-nowrap font-mono">{fmtValue(r.values[c])}</span>
                    ) : c === "methods" || c === "field_methods" || c === "lab_methods" ? (
                      <ol className="list-decimal pl-4">
                        {r.methods
                          .filter((m) => c === "methods" || (c === "field_methods" ? m.stage === "field" : m.stage === "lab"))
                          .map((m) => (
                            <li key={m.code} className="whitespace-nowrap">
                              {m.code}
                              {m.order === 1 && <span className="ml-1 text-[10px] text-emerald-700">(trọng tài)</span>}
                            </li>
                          ))}
                        {r.methods.length === 0 && r.cells[c]}
                      </ol>
                    ) : (
                      <span className="whitespace-pre-wrap">{r.cells[c] ?? ""}</span>
                    )}
                    {c === cols[1] && <RowNotes r={r} />}
                    {c === cols[1] && r.footnote_refs.length > 0 && (
                      <sup className="ml-1 text-sky-700">
                        {r.footnote_refs.map((f) => f.split("/").pop()?.replace("Footnote#", "")).join(",")}
                      </sup>
                    )}
                  </td>
                ))}
                <td className="px-2 py-1.5">
                  {r.rules.length > 0 && (
                    <div className="flex flex-col gap-0.5">
                      {Array.from(new Set(r.rules.map((x) => x.review_status))).map((s) => (
                        <StatusBadge key={s} status={s} label={`${s} ×${r.rules.filter((x) => x.review_status === s).length}`} />
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.header_rows.length > 0 && (
        <p className="text-xs text-slate-500">
          Header gốc: {table.header_rows.map((h) => h.filter(Boolean).join(" | ")).join(" // ")}
        </p>
      )}
      {footnotes.length > 0 && (
        <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
          <div className="mb-1 font-semibold">Chú thích</div>
          {footnotes.map(([id, t]) => (
            <p key={id} className="whitespace-pre-wrap">
              {t}
            </p>
          ))}
        </div>
      )}
      {table.warnings.length > 0 && (
        <ul className="text-xs text-amber-700">
          {table.warnings.map((w, i) => (
            <li key={i}>⚠ {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
