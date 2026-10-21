"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { admin, type QueryLogDetail } from "@/lib/admin";
import { getEvidencePath, graphPathsOf, type GraphCheck } from "@/lib/api";
import { AnswerView } from "@/components/chat/AnswerView";
import { EvidencePanel, type PathState } from "@/components/chat/EvidencePanel";
import { UnderstandingTags } from "@/components/chat/UnderstandingTags";
import { ms, usd, when } from "@/components/ui/Kpi";

export default function LogDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<QueryLogDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [paths, setPaths] = useState<PathState>({});

  useEffect(() => {
    admin
      .queryLog(id)
      .then(setD)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  async function select(chunk: string) {
    setSelected(chunk);
    if (!paths[chunk]) {
      setPaths((p) => ({ ...p, [chunk]: "loading" }));
      try {
        const path = await getEvidencePath(chunk);
        setPaths((p) => ({ ...p, [chunk]: path }));
      } catch {
        setPaths((p) => ({ ...p, [chunk]: "error" }));
      }
    }
    requestAnimationFrame(() => document.getElementById(`ev-log-${chunk}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }

  if (error) return <p className="rounded border border-rose-300 bg-rose-50 p-2 text-sm text-rose-800">{error}</p>;
  if (!d) return <p className="text-sm text-slate-500">Đang tải…</p>;

  const cited = new Set(d.citations.map((c) => c.chunk_id));
  const v = d.validation as { ok?: boolean; errors?: string[]; warnings?: string[]; rules_checked?: string[] };

  return (
    <main className="space-y-4">
      <Link href="/admin/logs" className="text-sm text-sky-700 hover:underline">
        ← Nhật ký
      </Link>
      <div className="rounded border border-slate-200 bg-white p-4">
        <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
          <span className="font-mono">{d.query_id}</span>
          <span>{when(d.created_at)}</span>
          {d.as_of_date && <span>tại {d.as_of_date}</span>}
          {d.client_id && <span className="font-mono">client {d.client_id}</span>}
          <Link href={`/?q=${encodeURIComponent(d.question)}`} className="ml-auto text-sky-700 hover:underline">
            Mở lại trong chat
          </Link>
        </div>
        <p className="mb-2 text-base font-medium">{d.question}</p>
        <UnderstandingTags u={d.understanding} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_420px]">
        <div className="space-y-4">
          <div className="rounded border border-slate-200 bg-white p-4">
            {d.answer ? (
              <AnswerView
                r={{
                  answer: d.answer,
                  citations: d.citations,
                  evidence: d.evidence,
                  status: d.status,
                  validation: d.validation,
                  path: d.path,
                  model: d.model,
                  thinking_level: d.thinking_level,
                  attempts: d.attempts,
                  latency_ms: d.latency_ms,
                  usage: d.usage,
                  cost_usd: d.cost_usd,
                  disclaimer: d.disclaimer,
                }}
                onCite={select}
              />
            ) : (
              <div className="text-sm">
                <p className="font-semibold text-rose-800">Không có câu trả lời được lưu (trạng thái {d.status}).</p>
                {d.error && <pre className="mt-2 whitespace-pre-wrap rounded bg-rose-50 p-2 text-xs text-rose-900">{d.error}</pre>}
              </div>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded border border-slate-200 bg-white p-3 text-xs">
              <div className="mb-1 font-semibold">Bộ kiểm tra trích dẫn</div>
              {v.ok === undefined ? (
                <p className="text-slate-500">Không chạy (đường đi {d.path}).</p>
              ) : (
                <>
                  <p className={v.ok ? "text-emerald-700" : "text-rose-700"}>{v.ok ? "Đạt" : "Từ chối"}</p>
                  {v.errors?.length ? (
                    <ul className="mt-1 list-disc pl-4 text-rose-800">
                      {v.errors.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  ) : null}
                  {v.warnings?.length ? (
                    <ul className="mt-1 list-disc pl-4 text-amber-800">
                      {v.warnings.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  ) : null}
                  {v.rules_checked?.length ? <p className="mt-1 text-slate-500">Quy tắc: {v.rules_checked.join(", ")}</p> : null}
                </>
              )}
            </div>
            <div className="rounded border border-slate-200 bg-white p-3 text-xs">
              <div className="mb-1 font-semibold">Vận hành</div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                <dt className="text-slate-500">Model</dt>
                <dd>
                  {d.model ?? "–"} · thinking {d.thinking_level ?? "–"} · {d.prompt_version}
                </dd>
                <dt className="text-slate-500">Lần sinh</dt>
                <dd>{d.attempts}</dd>
                <dt className="text-slate-500">Token</dt>
                <dd>
                  in {d.usage.prompt_tokens ?? 0} · out {d.usage.output_tokens ?? 0} · think {d.usage.thinking_tokens ?? 0}
                </dd>
                <dt className="text-slate-500">Chi phí</dt>
                <dd>{usd(d.cost_usd)}</dd>
                <dt className="text-slate-500">Độ trễ</dt>
                <dd>
                  {Object.entries(d.latency_ms)
                    .map(([k, val]) => `${k} ${ms(val)}`)
                    .join(" · ")}
                </dd>
              </dl>
              {d.error && d.answer && <pre className="mt-2 whitespace-pre-wrap rounded bg-rose-50 p-2 text-rose-900">{d.error}</pre>}
            </div>
          </div>

          <div className="rounded border border-slate-200 bg-white p-3 text-sm">
            <div className="mb-1 font-semibold">Phản hồi người dùng ({d.feedback.length})</div>
            {d.feedback.length === 0 ? (
              <p className="text-xs text-slate-500">Chưa có.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {d.feedback.map((f) => (
                  <li key={f.id} className="border-t border-slate-100 pt-1">
                    {f.rating > 0 ? "👍" : "👎"} <span className="text-slate-500">{when(f.created_at)}</span>
                    {f.comment && <div className="mt-0.5 whitespace-pre-wrap">{f.comment}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside className="rounded border border-slate-200 bg-white p-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
          <EvidencePanel
            turnId="log"
            evidence={d.evidence}
            cited={cited}
            selectedChunk={selected}
            paths={paths}
            onSelect={select}
            graphPaths={graphPathsOf(d)}
            graphMode={(d.validation as { graph?: GraphCheck | null }).graph?.mode}
          />
        </aside>
      </div>
    </main>
  );
}
