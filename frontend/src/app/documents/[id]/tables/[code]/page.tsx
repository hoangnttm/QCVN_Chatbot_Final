import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { TableView } from "@/components/TableView";

export const dynamic = "force-dynamic";

export default async function TablePage({ params }: { params: Promise<{ id: string; code: string }> }) {
  const { id, code } = await params;
  let table;
  try {
    table = await api.getTable(code);
  } catch (e) {
    if (String(e).startsWith("Error: 404")) notFound();
    throw e;
  }
  const dataRows = table.rows.filter((r) => !r.is_group_header).length;
  const rules = table.rows.reduce((n, r) => n + r.rules.length, 0);
  return (
    <main className="mx-auto max-w-7xl p-6">
      <Link href={`/documents/${encodeURIComponent(id)}`} className="text-sm text-sky-700 hover:underline">
        ← {id}
      </Link>
      <h1 className="mt-2 text-xl font-bold">
        {table.table_code}
        {table.title && <span className="font-normal text-slate-700"> — {table.title}</span>}
      </h1>
      <p className="mb-4 text-xs text-slate-500">
        {table.citation_label} · {dataRows} dòng giá trị{rules ? ` · ${rules} LimitRule` : ""} · dựng từ dữ liệu dòng đã
        chuẩn hóa (spec 4.4), không phải markdown gốc
      </p>
      <TableView table={table} />
    </main>
  );
}
