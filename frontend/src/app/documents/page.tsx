import Link from "next/link";
import { api, DOMAIN_LABEL } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

export const dynamic = "force-dynamic";

export default async function DocumentsPage() {
  const docs = await api.listDocuments();
  return (
    <main className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-2xl font-bold">Văn bản trong corpus</h1>
      <p className="mb-6 text-sm text-slate-600">
        Cấu trúc, bảng ngưỡng và quan hệ pháp lý được dựng lại từ dữ liệu đã chuẩn hóa (không phải file gốc). Bản dự
        thảo luôn được gắn nhãn.
      </p>
      <ul className="divide-y divide-slate-200 rounded border border-slate-200">
        {docs.map((d) => (
          <li key={d.document_id} className="flex flex-wrap items-center gap-3 p-4">
            <div className="min-w-0 flex-1">
              <Link href={`/documents/${encodeURIComponent(d.document_id)}`} className="font-semibold text-sky-800 hover:underline">
                {d.document_code}
              </Link>
              <div className="text-sm text-slate-700">{d.title}</div>
              <div className="mt-1 text-xs text-slate-500">
                {DOMAIN_LABEL[d.domain] ?? d.domain} · {d.chunks ?? 0} đoạn bằng chứng
                {d.version?.page_count ? ` · ${d.version.page_count} trang PDF` : ""}
              </div>
            </div>
            <StatusBadge status={d.text_status} />
          </li>
        ))}
      </ul>
    </main>
  );
}
