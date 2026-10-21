import Link from "next/link";
import { notFound } from "next/navigation";
import { api, DOMAIN_LABEL, FACT_LABEL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { StructureTree } from "@/components/StructureTree";

export const dynamic = "force-dynamic";

const REL_LABEL: Record<string, string> = {
  PROMULGATES: "ban hành",
  PROMULGATED_BY: "được ban hành bởi",
  REPLACES: "thay thế",
  REPEALS: "bãi bỏ",
  AMENDS: "sửa đổi",
  SUPPLEMENTS: "bổ sung",
  REFERENCES: "dẫn chiếu",
};

const KIND_LABEL: Record<string, string> = {
  transition: "Chuyển tiếp",
  roadmap: "Lộ trình",
  effective: "Hiệu lực",
  default_zone: "Cột mặc định",
};

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let doc, tree, monitoring;
  try {
    [doc, tree, monitoring] = await Promise.all([api.getDocument(id), api.getTree(id), api.getMonitoring(id)]);
  } catch (e) {
    if (String(e).startsWith("Error: 404")) notFound();
    throw e;
  }
  const outgoing = doc.relations.filter((r) => r.direction === "out" && r.type !== "REFERENCES");
  const incoming = doc.relations.filter((r) => r.direction === "in");
  const references = doc.relations.filter((r) => r.direction === "out" && r.type === "REFERENCES");

  return (
    <main className="mx-auto max-w-6xl p-6">
      <Link href="/documents" className="text-sm text-sky-700 hover:underline">
        ← Danh sách văn bản
      </Link>
      <header className="mb-6 mt-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold">{doc.document_code}</h1>
          <StatusBadge status={doc.text_status} />
          <span className="text-sm text-slate-500">{DOMAIN_LABEL[doc.domain] ?? doc.domain}</span>
        </div>
        <p className="text-slate-700">{doc.title}</p>
        {doc.issuer && <p className="text-sm text-slate-500">{doc.issuer}</p>}
        {doc.text_status === "draft" && (
          <p className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900">
            Văn bản này là <strong>bản dự thảo</strong>: số hiệu và ngày do quản trị viên khai báo (sổ đăng ký), nội dung
            có thể khác bản ký. Mọi trích dẫn từ văn bản này đều kèm cảnh báo.
          </p>
        )}
        {doc.note && <p className="mt-1 text-xs text-slate-500">{doc.note}</p>}
      </header>

      <div className="grid gap-6 md:grid-cols-3">
        <section className="rounded border border-slate-200 p-4">
          <h2 className="mb-2 font-semibold">Sổ đăng ký</h2>
          <dl className="text-sm">
            {doc.facts.map((f, i) => (
              <div key={i} className="mb-1 flex flex-wrap gap-x-2">
                <dt className="w-32 shrink-0 text-slate-500">{FACT_LABEL[f.fact_type] ?? f.fact_type}</dt>
                <dd className="flex-1">
                  {f.value}
                  {f.scope && <span className="text-slate-500"> ({f.scope})</span>}
                  <div className="text-[11px] text-slate-500">
                    {f.source === "admin_declared" ? `do quản trị viên khai báo${f.declared_at ? " " + f.declared_at.slice(0, 10) : ""}` : f.evidence}
                  </div>
                </dd>
              </div>
            ))}
          </dl>
          {doc.version && (
            <p className="mt-2 break-all text-[11px] text-slate-400">
              {doc.version.file_name} · sha256 {doc.version.sha256.slice(0, 12)}…
            </p>
          )}
        </section>

        <section className="rounded border border-slate-200 p-4">
          <h2 className="mb-2 font-semibold">Quan hệ pháp lý</h2>
          <ul className="space-y-1 text-sm">
            {outgoing.map((r, i) => (
              <li key={i}>
                <span className="font-medium">{REL_LABEL[r.type] ?? r.type}</span>{" "}
                {r.target_document_id ? (
                  <Link href={`/documents/${encodeURIComponent(r.target_document_id)}`} className="text-sky-700 hover:underline">
                    {r.target}
                  </Link>
                ) : (
                  r.target
                )}
                {r.scope === "partial" && <span className="text-slate-600"> — {r.target_part}</span>}
                {r.condition && <div className="text-[11px] text-slate-500">{r.condition}</div>}
                <div className="text-[11px] text-slate-400">
                  {r.origin === "registry" ? "sổ đăng ký" : "trích từ văn bản"}: {r.evidence ?? r.citation_label}
                </div>
              </li>
            ))}
            {incoming.map((r, i) => (
              <li key={`in${i}`} className="text-slate-600">
                {REL_LABEL[r.type] ?? r.type} bởi{" "}
                <Link href={`/documents/${encodeURIComponent(doc.relations[0] ? r.source : "")}`} className="text-sky-700 hover:underline">
                  {r.source}
                </Link>
              </li>
            ))}
          </ul>
          {references.length > 0 && (
            <p className="mt-2 text-xs text-slate-500">Dẫn chiếu: {references.map((r) => r.target).join("; ")}</p>
          )}
        </section>

        <section className="rounded border border-slate-200 p-4">
          <h2 className="mb-2 font-semibold">Số liệu đã chuẩn hóa</h2>
          <dl className="grid grid-cols-2 gap-x-2 text-sm">
            {Object.entries(doc.counts).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-slate-500">{k}</dt>
                <dd className="text-right font-mono">{v}</dd>
              </div>
            ))}
          </dl>
          {Object.keys(doc.review).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(doc.review).map(([s, n]) => (
                <StatusBadge key={s} status={s} label={`LimitRule ${s}: ${n}`} />
              ))}
            </div>
          )}
          {doc.tables.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {doc.tables.map((t) => (
                <li key={t.table_code}>
                  <Link
                    href={`/documents/${encodeURIComponent(id)}/tables/${encodeURIComponent(t.table_code)}`}
                    className="text-sky-700 hover:underline"
                  >
                    {t.table_code}
                  </Link>{" "}
                  <span className="text-slate-600">{t.title}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {doc.transition_rules.length > 0 && (
        <section className="mt-6 rounded border border-slate-200 p-4">
          <h2 className="mb-2 font-semibold">Chuyển tiếp / lộ trình / hiệu lực</h2>
          <ul className="space-y-2 text-sm">
            {doc.transition_rules.map((t) => (
              <li key={t.rule_id}>
                <span className="rounded bg-slate-100 px-1.5 text-xs">{KIND_LABEL[t.kind] ?? t.kind}</span>{" "}
                <span className="text-slate-800">{t.text}</span>
                <div className="text-[11px] text-slate-500">
                  {t.citation_label}
                  {t.dates.length > 0 && ` · ngày: ${t.dates.join(", ")}`}
                  {t.relative_dates.length > 0 && ` · mốc quy đổi: ${t.relative_dates.join(", ")}`}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {monitoring.length > 0 && (
        <section className="mt-6 rounded border border-slate-200 p-4">
          <h2 className="mb-2 font-semibold">Yêu cầu quan trắc ({monitoring.length})</h2>
          <ul className="space-y-2 text-sm">
            {monitoring.map((q) => (
              <li key={q.requirement_id}>
                <span className="rounded bg-slate-100 px-1.5 text-xs">{q.requirement_type}</span>{" "}
                <span className="text-xs text-slate-500">{DOMAIN_LABEL[q.domain] ?? q.domain}</span>{" "}
                <StatusBadge status={q.review_status} label={q.review_status} />
                <div className="text-slate-800">{q.summary}</div>
                <div className="text-[11px] text-slate-500">{q.citation_label}</div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-6">
        <h2 className="mb-2 font-semibold">Cấu trúc văn bản ({tree.nodes} node)</h2>
        <StructureTree nodes={tree.tree} docId={id} />
      </section>
    </main>
  );
}
