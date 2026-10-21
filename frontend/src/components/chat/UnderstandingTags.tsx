import { DOMAIN_LABEL, INTENT_LABEL, type Understanding } from "@/lib/api";

export function Tag({ children }: { children: React.ReactNode }) {
  return <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5">{children}</span>;
}

export function UnderstandingTags({ u }: { u: Understanding | null | undefined }) {
  if (!u) return null;
  return (
    <div className="flex flex-wrap gap-1 text-xs text-slate-600">
      <Tag>{DOMAIN_LABEL[u.domain] ?? u.domain}</Tag>
      <Tag>{INTENT_LABEL[u.intent] ?? u.intent}</Tag>
      {(u.parameters ?? []).map((p) => (
        <Tag key={p}>{p}</Tag>
      ))}
      {u.zone && <Tag>Cột/vùng {u.zone}</Tag>}
      {u.flow_m3_day != null && <Tag>F = {u.flow_m3_day} m³/ngày</Tag>}
      {u.policy && <Tag>Chính sách {u.policy}</Tag>}
    </div>
  );
}
