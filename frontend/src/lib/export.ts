/** Markdown export of one answered turn (M20): conclusion, basis with [n] citations, conditions,
 *  missing information, warnings, the citation list and the disclaimer. */

import type { QAResponse } from "@/lib/api";

export type ExportTurn = { question: string; asOf: string | null; response: QAResponse };

export function turnToMarkdown(turn: ExportTurn): string {
  const r = turn.response;
  const index = new Map(r.evidence.map((e, i) => [e.chunk_id, i + 1]));
  const cite = (ids: string[]) =>
    ids
      .map((id) => index.get(id))
      .filter((n): n is number => n !== undefined)
      .map((n) => `[${n}]`)
      .join("");
  const lines: string[] = [];
  lines.push(`# ${turn.question}`);
  lines.push("");
  lines.push(`Thời điểm áp dụng: ${turn.asOf ?? "hiện tại"} · Mã tra cứu: ${r.query_id}`);
  lines.push("");
  lines.push(`## Kết luận${r.answer.abstained ? " (không đủ căn cứ)" : ""}`);
  lines.push("");
  lines.push(`${r.answer.conclusion} ${cite(r.answer.citations)}`.trim());
  if (r.answer.abstain_reason) lines.push(`\n_${r.answer.abstain_reason}_`);
  if (r.answer.basis.length) {
    lines.push("", "## Căn cứ", "");
    for (const b of r.answer.basis) lines.push(`- ${b.text} ${cite(b.citations)}`.trim());
  }
  if (r.answer.conditions.length) {
    lines.push("", "## Điều kiện áp dụng", "");
    for (const c of r.answer.conditions) lines.push(`- ${c}`);
  }
  if (r.answer.missing_information.length) {
    lines.push("", "## Cần thêm thông tin", "");
    for (const c of r.answer.missing_information) lines.push(`- ${c}`);
  }
  if (r.answer.warnings.length) {
    lines.push("", "## Cảnh báo", "");
    for (const w of r.answer.warnings) lines.push(`- ⚠ ${w}`);
  }
  const cited = r.evidence.filter((e) => r.citations.some((c) => c.chunk_id === e.chunk_id));
  if (cited.length) {
    lines.push("", "## Trích dẫn", "");
    for (const e of cited) {
      const draft = e.text_status === "draft" ? " (bản dự thảo)" : "";
      lines.push(`- [${index.get(e.chunk_id)}] ${e.citation_label} — ${e.document_code}${draft}`);
    }
  }
  lines.push("", "---", "", `_${r.disclaimer}_`, "");
  return lines.join("\n");
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
