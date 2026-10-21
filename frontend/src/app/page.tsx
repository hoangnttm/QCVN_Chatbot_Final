"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  askStream,
  getEvidencePath,
  getHistory,
  getQuestion,
  getQuota,
  sendFeedback,
  type EvidenceItem,
  type HistoryItem,
  type QAResponse,
  type QuotaStatus,
  type StructuredHit,
  type Understanding,
  graphPathsOf,
} from "@/lib/api";
import { friendlyError, friendlyStreamError, type FriendlyError } from "@/lib/errors";
import { copyText, turnToMarkdown } from "@/lib/export";
import { AnswerView } from "@/components/chat/AnswerView";
import { EvidencePanel, type PathState } from "@/components/chat/EvidencePanel";
import { HistorySidebar } from "@/components/chat/HistorySidebar";
import { UnderstandingTags } from "@/components/chat/UnderstandingTags";
import { Drawer } from "@/components/ui/Drawer";
import { showToast } from "@/components/ui/Toast";

type Turn = {
  id: number;
  queryId?: string;
  question: string;
  asOf: string | null;
  createdAt?: string;
  stage: string;
  understanding?: Understanding;
  evidence: EvidenceItem[];
  structured: StructuredHit[];
  retrievalWarnings: string[];
  deltaChars: number;
  response?: QAResponse;
  error?: FriendlyError;
  stopped?: boolean;
  fromHistory?: boolean;
  feedback?: 1 | -1;
};

const STAGE_LABEL: Record<string, string> = {
  understanding: "Đang hiểu câu hỏi và tìm căn cứ…",
  generating: "Đang soạn câu trả lời từ căn cứ…",
  regenerating: "Câu trả lời bị bộ kiểm tra trích dẫn từ chối — đang sinh lại…",
  validating: "Đang kiểm tra trích dẫn…",
};

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatInner />
    </Suspense>
  );
}

function ChatInner() {
  const sp = useSearchParams();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [samples, setSamples] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    api
      .getCorpus()
      .then((c) => alive && setSamples(c.sample_questions))
      .catch(() => undefined); // sample questions are optional
    return () => {
      alive = false;
    };
  }, []);
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<string | null>(null);
  const [paths, setPaths] = useState<PathState>({});
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [histOpen, setHistOpen] = useState(false);
  const [evOpen, setEvOpen] = useState(false);
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [printId, setPrintId] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  const abortRef = useRef<AbortController | null>(null);

  const patch = (id: number, fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)));

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      setHistory(await getHistory(50));
    } catch {
      /* history is best-effort */
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const loadQuota = useCallback(async () => {
    try {
      setQuota(await getQuota());
    } catch {
      /* best-effort */
    }
  }, []);

  useEffect(() => {
    void loadHistory();
    void loadQuota();
  }, [loadHistory, loadQuota]);

  // Deep link /?q=... prefills the question (no auto-submit: each question costs an LLM call).
  useEffect(() => {
    const q = sp.get("q");
    if (q) {
      setQuestion(q);
      textareaRef.current?.focus();
    }
  }, [sp]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, busy]);

  useEffect(() => {
    if (printId === null) return;
    const done = () => setPrintId(null);
    window.addEventListener("afterprint", done);
    const t = setTimeout(() => window.print(), 50);
    return () => {
      clearTimeout(t);
      window.removeEventListener("afterprint", done);
    };
  }, [printId]);

  async function submit(q: string, asOfOverride?: string | null) {
    const text = q.trim();
    if (!text || busy) return;
    const id = nextId.current++;
    const effectiveAsOf = asOfOverride !== undefined ? asOfOverride : asOf || null;
    const turn: Turn = {
      id,
      question: text,
      asOf: effectiveAsOf,
      stage: "understanding",
      evidence: [],
      structured: [],
      retrievalWarnings: [],
      deltaChars: 0,
    };
    setTurns((ts) => [...ts, turn]);
    setSelectedTurn(id);
    setSelectedChunk(null);
    setQuestion("");
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await askStream(
        { question: text, as_of_date: effectiveAsOf },
        (e) => {
          switch (e.event) {
            case "stage":
              patch(id, (t) => ({ ...t, stage: e.data.stage, queryId: e.data.query_id ?? t.queryId }));
              break;
            case "understanding":
              patch(id, (t) => ({ ...t, understanding: e.data.understanding }));
              break;
            case "evidence":
              patch(id, (t) => ({
                ...t,
                evidence: e.data.evidence,
                structured: e.data.structured_hits,
                retrievalWarnings: e.data.warnings,
              }));
              break;
            case "delta":
              patch(id, (t) => ({ ...t, deltaChars: t.deltaChars + e.data.text.length }));
              break;
            case "answer":
              patch(id, (t) => ({
                ...t,
                response: e.data,
                queryId: e.data.query_id,
                evidence: e.data.evidence,
                stage: "done",
              }));
              break;
            case "error":
              patch(id, (t) => ({ ...t, error: friendlyStreamError(e.data), queryId: e.data.query_id ?? t.queryId, stage: "error" }));
              break;
          }
        },
        ac.signal,
      );
    } catch (err) {
      const fe = friendlyError(err);
      patch(id, (t) => ({ ...t, error: fe, stage: "error", stopped: fe.code === "aborted" }));
    } finally {
      abortRef.current = null;
      setBusy(false);
      void loadHistory();
      void loadQuota();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  async function openHistory(item: HistoryItem) {
    setHistOpen(false);
    const existing = turns.find((t) => t.queryId === item.query_id);
    if (existing) {
      setSelectedTurn(existing.id);
      requestAnimationFrame(() => document.getElementById(`turn-${existing.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
      return;
    }
    try {
      const s = await getQuestion(item.query_id);
      const id = nextId.current++;
      const response: QAResponse | undefined =
        s.answer && s.status !== "error" && s.path !== "error"
          ? {
              query_id: s.query_id,
              question: s.question,
              as_of_date: s.as_of_date,
              understanding: s.understanding,
              structured_hits: [],
              evidence: s.evidence,
              answer: s.answer,
              citations: s.citations,
              validation: (s.validation as QAResponse["validation"]) ?? { ok: true, errors: [], warnings: [], rules_checked: [] },
              status: s.status,
              path: s.path,
              attempts: s.attempts,
              model: s.model,
              thinking_level: s.thinking_level,
              prompt_version: s.prompt_version,
              usage: s.usage,
              cost_usd: s.cost_usd ?? 0,
              latency_ms: s.latency_ms,
              disclaimer: s.disclaimer,
            }
          : undefined;
      const turn: Turn = {
        id,
        queryId: s.query_id,
        question: s.question,
        asOf: s.as_of_date,
        createdAt: s.created_at,
        stage: response ? "done" : "error",
        understanding: s.understanding,
        evidence: s.evidence,
        structured: [],
        retrievalWarnings: [],
        deltaChars: 0,
        response,
        error: response
          ? undefined
          : { title: "Câu hỏi này đã gặp lỗi khi xử lý", hint: s.error ?? "Không có câu trả lời được lưu.", requestId: s.query_id, code: "stored_error" },
        fromHistory: true,
      };
      setTurns((ts) => [...ts, turn]);
      setSelectedTurn(id);
      setSelectedChunk(null);
    } catch (err) {
      const fe = friendlyError(err);
      showToast(`${fe.title}: ${fe.hint}`, "error", 6000);
    }
  }

  async function showChunk(turnId: number, chunkId: string) {
    setSelectedTurn(turnId);
    setSelectedChunk(chunkId);
    if (window.matchMedia("(max-width: 1023px)").matches) setEvOpen(true);
    if (!paths[chunkId]) {
      setPaths((p) => ({ ...p, [chunkId]: "loading" }));
      try {
        const path = await getEvidencePath(chunkId);
        setPaths((p) => ({ ...p, [chunkId]: path }));
      } catch {
        setPaths((p) => ({ ...p, [chunkId]: "error" }));
      }
    }
    requestAnimationFrame(() =>
      document.getElementById(`ev-${turnId}-${chunkId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }),
    );
  }

  async function rate(turn: Turn, rating: 1 | -1, comment?: string) {
    if (!turn.queryId) return;
    try {
      await sendFeedback(turn.queryId, rating, comment);
      patch(turn.id, (t) => ({ ...t, feedback: rating }));
      showToast("Cảm ơn phản hồi của bạn", "success");
    } catch (err) {
      const fe = friendlyError(err);
      showToast(`Không gửi được đánh giá: ${fe.title}`, "error");
    }
  }

  async function copyTurn(turn: Turn) {
    if (!turn.response) return;
    const ok = await copyText(turnToMarkdown({ question: turn.question, asOf: turn.asOf, response: turn.response }));
    showToast(ok ? "Đã sao chép câu trả lời (Markdown)" : "Trình duyệt không cho phép sao chép", ok ? "success" : "error");
  }

  const current = turns.find((t) => t.id === selectedTurn) ?? turns[turns.length - 1];
  const cited = new Set(current?.response?.citations.map((c) => c.chunk_id) ?? []);
  const softNotice = quota && (quota.over_quota || quota.budget.ratio >= 0.8);

  const evidencePanel = current ? (
    <EvidencePanel
      turnId={current.id}
      evidence={current.evidence}
      structured={current.structured}
      cited={cited}
      selectedChunk={selectedChunk}
      paths={paths}
      onSelect={(chunk) => showChunk(current.id, chunk)}
      graphPaths={graphPathsOf(current.response)}
      graphMode={current.response?.validation?.graph?.mode}
    />
  ) : (
    <div className="rounded border border-dashed border-slate-300 p-4 text-sm text-slate-500">
      Căn cứ (đoạn văn bản được truy xuất) sẽ hiện ở đây; bấm số [n] trong câu trả lời để xem đoạn và vị trí trong văn
      bản.
    </div>
  );

  const historyPanel = (
    <HistorySidebar items={history} loading={historyLoading} activeId={current?.queryId ?? null} onOpen={openHistory} onRefresh={loadHistory} />
  );

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-1 gap-4 px-4 py-4 sm:px-6 lg:gap-6">
      <aside data-print="hide" className="hidden w-60 shrink-0 lg:block print:hidden">
        <div className="sticky top-4 max-h-[calc(100vh-2rem)] overflow-hidden rounded border border-slate-200 bg-white p-3">{historyPanel}</div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div className="mb-3 flex items-center gap-2 lg:hidden print:hidden">
          <button type="button" onClick={() => setHistOpen(true)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
            Lịch sử
          </button>
          {current && current.evidence.length > 0 && (
            <button type="button" onClick={() => setEvOpen(true)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
              Căn cứ ({current.evidence.length})
            </button>
          )}
        </div>

        {turns.length === 0 && (
          <div className="mb-6 rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 print:hidden">
            <p className="mb-2 font-semibold">Tra cứu quy chuẩn kỹ thuật môi trường có trích dẫn.</p>
            <p className="mb-3">
              Mọi con số trong câu trả lời đều được đối chiếu với đoạn văn bản được trích; khi không đủ căn cứ hệ thống sẽ nói rõ
              thay vì đoán. Đây không phải kết luận pháp lý.
            </p>
            <div className="flex flex-wrap gap-2">
              {samples.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => submit(s)}
                  className="rounded-full border border-sky-300 bg-white px-3 py-1.5 text-left text-xs text-sky-800 hover:bg-sky-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-1 flex-col gap-6">
          {turns.map((t) => (
            <TurnView
              key={t.id}
              turn={t}
              active={current?.id === t.id}
              printing={printId === t.id}
              busy={busy}
              onSelect={() => setSelectedTurn(t.id)}
              onCite={(chunk) => showChunk(t.id, chunk)}
              onRate={(r, c) => rate(t, r, c)}
              onCopy={() => copyTurn(t)}
              onPrint={() => setPrintId(t.id)}
              onRetry={() => submit(t.question, t.asOf)}
              onEvidence={() => {
                setSelectedTurn(t.id);
                setEvOpen(true);
              }}
            />
          ))}
          <div ref={bottomRef} />
        </div>

        <form
          className="sticky bottom-0 mt-6 flex flex-col gap-2 border-t border-slate-200 bg-white pt-3 print:hidden"
          style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 0.5rem)" }}
          onSubmit={(e) => {
            e.preventDefault();
            submit(question);
          }}
        >
          {softNotice && quota && (
            <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {quota.over_quota
                ? `Trình duyệt này đã dùng ${quota.used}/${quota.limit} câu hỏi trong ngày. Bạn vẫn có thể hỏi tiếp; quản trị viên được thông báo để cân đối chi phí.`
                : `Ngân sách Gemini hôm nay đã dùng ${Math.round(quota.budget.ratio * 100)} %. Câu hỏi vẫn được xử lý; vui lòng ưu tiên câu hỏi cần thiết.`}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(question);
              }
            }}
            rows={2}
            placeholder="Đặt câu hỏi về ngưỡng, phương pháp, phạm vi áp dụng, hiệu lực… (Enter để gửi, Shift+Enter xuống dòng)"
            className="w-full rounded border border-slate-300 p-3 text-sm focus:border-sky-500 focus:outline-none"
          />
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-slate-600">
              Thời điểm áp dụng
              <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="rounded border border-slate-300 px-2 py-1 text-sm" />
            </label>
            {busy ? (
              <button type="button" onClick={stop} className="ml-auto rounded border border-rose-300 px-4 py-2 font-semibold text-rose-700">
                Dừng
              </button>
            ) : (
              <button
                type="submit"
                disabled={!question.trim()}
                className="ml-auto rounded bg-sky-700 px-4 py-2 font-semibold text-white disabled:opacity-50"
              >
                Hỏi
              </button>
            )}
          </div>
          <p className="text-[11px] text-slate-500">
            Câu hỏi được xử lý bởi Google Gemini và được lưu nhật ký ẩn danh (mã trình duyệt) để cải thiện chất lượng. Không phải tư vấn
            pháp lý.
          </p>
        </form>
      </main>

      <aside data-print="hide" className="hidden w-[400px] shrink-0 lg:block print:hidden">
        <div className="sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto rounded border border-slate-200 bg-white p-3">{evidencePanel}</div>
      </aside>

      <Drawer open={histOpen} onClose={() => setHistOpen(false)} title="Lịch sử câu hỏi" side="left">
        {historyPanel}
      </Drawer>
      <Drawer open={evOpen} onClose={() => setEvOpen(false)} title={`Căn cứ${current ? ` (${current.evidence.length})` : ""}`} side="bottom">
        {evidencePanel}
      </Drawer>
    </div>
  );
}

function TurnView({
  turn,
  active,
  printing,
  busy,
  onSelect,
  onCite,
  onRate,
  onCopy,
  onPrint,
  onRetry,
  onEvidence,
}: {
  turn: Turn;
  active: boolean;
  printing: boolean;
  busy: boolean;
  onSelect: () => void;
  onCite: (chunk: string) => void;
  onRate: (r: 1 | -1, comment?: string) => void;
  onCopy: () => void;
  onPrint: () => void;
  onRetry: () => void;
  onEvidence: () => void;
}) {
  const r = turn.response;
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const citedEvidence = r ? turn.evidence.filter((e) => r.citations.some((c) => c.chunk_id === e.chunk_id)) : [];
  const index = new Map(turn.evidence.map((e, i) => [e.chunk_id, i + 1]));

  return (
    <section
      id={`turn-${turn.id}`}
      onClick={onSelect}
      data-print-active={printing ? "true" : "false"}
      className={`print-turn rounded border p-4 ${active ? "border-sky-300 bg-white" : "border-slate-200 bg-white/70"}`}
    >
      <div className="mb-3 flex flex-wrap items-start gap-2">
        <div className="rounded bg-slate-100 px-3 py-2 text-sm font-medium text-slate-800">{turn.question}</div>
        {turn.asOf && <span className="text-xs text-slate-500">tại {turn.asOf}</span>}
        {turn.fromHistory && turn.createdAt && (
          <span className="text-xs text-slate-500">từ lịch sử · {new Date(turn.createdAt).toLocaleString("vi-VN")}</span>
        )}
      </div>

      {turn.understanding && (
        <div className="mb-2">
          <UnderstandingTags u={turn.understanding} />
        </div>
      )}

      {!r && !turn.error && (
        <p className="text-sm text-slate-600">
          {STAGE_LABEL[turn.stage] ?? turn.stage}
          {turn.deltaChars > 0 && turn.stage === "generating" && ` (${turn.deltaChars} ký tự)`}
          {turn.evidence.length > 0 && ` · ${turn.evidence.length} đoạn căn cứ`}
        </p>
      )}

      {turn.error && (
        <div className={`rounded border p-3 text-sm ${turn.stopped ? "border-slate-300 bg-slate-50" : "border-rose-200 bg-rose-50"}`}>
          <div className={`font-semibold ${turn.stopped ? "text-slate-800" : "text-rose-800"}`}>{turn.error.title}</div>
          <div className="text-xs text-slate-700">{turn.error.hint}</div>
          {turn.stopped && (
            <div className="mt-1 text-xs text-slate-500">
              Máy chủ vẫn hoàn tất và ghi nhật ký câu hỏi này; bạn có thể xem lại trong Lịch sử sau vài giây.
            </div>
          )}
          {turn.error.requestId && !turn.stopped && <div className="mt-1 font-mono text-[11px] text-slate-500">mã: {turn.error.requestId}</div>}
          {!turn.fromHistory && (
            <button
              type="button"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation();
                onRetry();
              }}
              className="mt-2 rounded border border-slate-300 bg-white px-3 py-1 text-xs font-semibold disabled:opacity-50"
            >
              Thử lại
            </button>
          )}
        </div>
      )}

      {r && (
        <>
          <AnswerView
            r={r}
            extraWarnings={turn.retrievalWarnings}
            onCite={onCite}
            footer={
              <span className="ml-auto flex flex-wrap items-center gap-1 print:hidden">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onEvidence();
                  }}
                  className="rounded border border-slate-300 px-2 py-1 lg:hidden"
                >
                  Căn cứ ({turn.evidence.length})
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCopy();
                  }}
                  className="rounded border border-slate-300 px-2 py-1"
                  title="Sao chép câu trả lời dạng Markdown"
                >
                  Sao chép
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onPrint();
                  }}
                  className="rounded border border-slate-300 px-2 py-1"
                  title="In hoặc lưu PDF lượt hỏi này"
                >
                  In / PDF
                </button>
                <span className="ml-1">Hữu ích?</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setCommentOpen(false);
                    onRate(1);
                  }}
                  className={`min-h-8 min-w-8 rounded border px-2 py-1 ${turn.feedback === 1 ? "border-emerald-400 bg-emerald-100" : "border-slate-300"}`}
                >
                  👍
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setCommentOpen((v) => !v);
                  }}
                  className={`min-h-8 min-w-8 rounded border px-2 py-1 ${turn.feedback === -1 ? "border-rose-400 bg-rose-100" : "border-slate-300"}`}
                >
                  👎
                </button>
              </span>
            }
          />
          {commentOpen && (
            <div className="mt-2 flex flex-col gap-1 rounded border border-slate-200 bg-slate-50 p-2 text-xs print:hidden" onClick={(e) => e.stopPropagation()}>
              <label className="text-slate-600">Điều gì chưa đúng? (tùy chọn)</label>
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                rows={2}
                className="w-full rounded border border-slate-300 bg-white p-2"
                placeholder="ví dụ: sai cột, thiếu điều kiện lưu lượng, trích dẫn không đúng…"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    onRate(-1, comment.trim() || undefined);
                    setCommentOpen(false);
                    setComment("");
                  }}
                  className="rounded bg-slate-800 px-3 py-1 font-semibold text-white"
                >
                  Gửi phản hồi
                </button>
                <button type="button" onClick={() => setCommentOpen(false)} className="rounded border border-slate-300 px-3 py-1">
                  Hủy
                </button>
              </div>
            </div>
          )}
          {/* Printed copy: the cited passages, so the PDF stands on its own. */}
          {citedEvidence.length > 0 && (
            <div className="print-only mt-4 border-t border-slate-300 pt-3 text-xs">
              <h4 className="mb-1 font-semibold">Trích dẫn</h4>
              <ol className="space-y-2">
                {citedEvidence.map((e) => (
                  <li key={e.chunk_id}>
                    <div className="font-semibold">
                      [{index.get(e.chunk_id)}] {e.citation_label} — {e.document_code}
                      {e.text_status === "draft" && " (bản dự thảo)"}
                    </div>
                    <div className="whitespace-pre-wrap">{e.text}</div>
                  </li>
                ))}
              </ol>
              <p className="mt-2 text-[10px] text-slate-500">
                In từ QCVN Chatbot · mã tra cứu {r.query_id} · {new Date().toLocaleString("vi-VN")}
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
