/** Admin API client: shared token in localStorage (until M9), `X-Admin-Token` on every call,
 *  typed responses for /api/v1/admin/*. */

import { API_URL, ApiError, type Answer, type Citation, type EvidenceItem, type Understanding } from "@/lib/api";

const TOKEN_KEY = "qcvn.admin_token";
export const REVIEWER_KEY = "qcvn.reviewer";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

async function parseError(res: Response, path: string): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = await res.json();
  } catch {
    /* no body */
  }
  const d = (detail as { detail?: unknown })?.detail ?? detail;
  const code = typeof d === "object" && d !== null && "code" in d ? String((d as { code: unknown }).code) : undefined;
  const text = typeof d === "string" ? d : d ? JSON.stringify(d) : `${res.status} ${res.statusText}`;
  return new ApiError(res.status, text, { code, requestId: res.headers.get("x-request-id") ?? undefined, path, detailObject: d });
}

export async function adminFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
    "X-Admin-Token": token ?? "",
  };
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
  if (res.status === 401) {
    clearToken();
    throw new ApiError(401, "Token quản trị không hợp lệ", { code: "unauthorized", path });
  }
  if (!res.ok) throw await parseError(res, path);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function adminDownload(path: string, filename: string): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, { headers: { "X-Admin-Token": getToken() ?? "" } });
  if (!res.ok) throw await parseError(res, path);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------- types

export type HealthDetails = {
  status: "ok" | "degraded";
  version: string;
  uptime_s: number;
  checks: {
    database: { ok: boolean; latency_ms: number };
    index: { ok: boolean; active_label: string | null; bm25_loaded: boolean; bm25_docs: number | null };
    neo4j: { ok: boolean | null; skipped?: boolean; error?: string };
    gemini: {
      configured: boolean;
      model: string;
      last_success_at: string | null;
      last_error_at: string | null;
      last_error: string | null;
      consecutive_errors: number;
      calls: number;
      errors: number;
      probe?: { ok: boolean; error?: string };
    };
  };
  alerts_active: number;
};

export type Metrics = {
  window_min: number;
  since: string;
  questions: number;
  by_status: Record<string, number>;
  by_path: Record<string, number>;
  error_rate: number;
  abstain_rate: number;
  validator_reject_rate: number;
  llm_questions: number;
  latency_ms: { p50: number | null; p95: number | null; max: number | null };
  llm: {
    uptime_s: number;
    llm_calls: number;
    llm_errors: number;
    llm_consecutive_errors: number;
    llm_last_success_at: string | null;
    llm_last_error_at: string | null;
    llm_last_error: string | null;
  };
  cost_usd: { window: number; today: number; month: number };
  budget: { limit_usd: number; spent_usd: number; ratio: number };
  feedback: { negative: number; positive: number };
  quota: { per_client_limit: number; clients_over_quota: { client_key: string; questions: number; cost_usd: number }[] };
  top_clients_today: { client_key: string; questions: number; cost_usd: number }[];
};

export type AlertEvent = {
  id: number;
  rule: string;
  severity: "info" | "warn" | "crit";
  dedup_key: string;
  message: string;
  payload: Record<string, unknown>;
  fired_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  notified: boolean;
  notify_error: string | null;
};

export type QueryLogItem = {
  query_id: string;
  question: string;
  created_at: string;
  client_id: string | null;
  domain: string;
  intent: string;
  status: "answered" | "abstained" | "failed" | "error";
  path: "policy" | "llm" | "fallback" | "error";
  model_id: string | null;
  attempts: number;
  latency_total_ms: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  validation_ok: boolean | null;
  abstained: boolean | null;
  feedback_min: number | null;
  feedback_max: number | null;
  feedback_count: number;
  error: string | null;
};

export type QueryLogList = { total: number; limit: number; offset: number; items: QueryLogItem[] };

export type QueryLogDetail = {
  query_id: string;
  question: string;
  as_of_date: string | null;
  understanding: Understanding;
  structured_hits: { rule_code: string }[];
  evidence: EvidenceItem[];
  answer: Answer | null;
  citations: Citation[];
  validation: { ok: boolean; errors: string[]; warnings: string[]; rules_checked: string[] } | Record<string, never>;
  status: QueryLogItem["status"];
  path: QueryLogItem["path"];
  attempts: number;
  model: string | null;
  thinking_level: string | null;
  prompt_version: string;
  usage: Record<string, number>;
  latency_ms: Record<string, number>;
  cost_usd: number | null;
  error: string | null;
  disclaimer: string;
  created_at: string;
  client_id: string | null;
  feedback: { id: number; rating: number; comment: string | null; created_at: string }[];
};

export type ReviewDecision = {
  id: number;
  target_type: string;
  target_id: string;
  decision: "approved" | "rejected" | "needs_recheck";
  correction: string | null;
  note: string | null;
  reviewer: string;
  reviewed_at: string;
  source: string | null;
};

export type ReviewSummary = {
  tables: {
    table_code: string;
    document_id: string;
    title: string | null;
    rules_by_status: Record<string, number>;
    rules_total: number;
    rows_total: number;
    sample_required: number;
    rows_reviewed: number;
    all_sampled_approved: boolean;
    blocking_rows: { row_id: string; status: string; reviewer: string; note: string | null }[];
    last_decision_at: string | null;
  }[];
  transition_rules: { by_status: Record<string, number> };
  document_relations: { by_status: Record<string, number>; high_risk_types: string[] };
  monitoring_requirements: { by_status: Record<string, number> };
  generated_at: string;
};

export type ReviewRule = {
  rule_code: string;
  zone: string;
  op: string;
  value: number | null;
  min: number | null;
  max: number | null;
  ref_o2: number | null;
  unit_raw: string;
  unit_id: string | null;
  parameter_id: string | null;
  parameter_raw: string;
  equipment_type_id: string | null;
  conditions: Record<string, unknown>[];
  exceptions: string[];
  review_status: string;
  citation_label: string;
};

export type ReviewRow = {
  row_id: string;
  row_no: string;
  row_path: string[];
  is_group_header: boolean;
  inherits_from: string | null;
  citation_label: string;
  cells: Record<string, string>;
  raw_cells: Record<string, string>;
  entities: Record<string, unknown>;
  values: Record<string, { op: string; value: number | null; min: number | null; max: number | null; ref_o2: number | null; raw: string }>;
  conditions: { type: string; op?: string | null; value?: unknown; unit?: string | null; text?: string | null }[];
  exceptions: string[];
  footnote_refs: string[];
  parse_warnings: string[];
  line_start: number;
  printed_page: string | null;
  risk_score: number;
  in_sample: boolean;
  review_status: string;
  rules: ReviewRule[];
  last_decision: ReviewDecision | null;
};

export type ReviewTable = {
  table: { table_code: string; document_id: string; title: string | null; columns: string[]; header_rows: string[][]; citation_label: string | null };
  sample: { n: number; required: number; reviewed_rows: number; ok: boolean; blocking_rows: ReviewSummary["tables"][number]["blocking_rows"]; row_ids: string[] };
  rules_by_status: Record<string, number>;
  rows: ReviewRow[];
};

export type ReviewRowDetail = ReviewRow & {
  table_code: string;
  evidence: { chunk_id: string; citation_label: string; text: string; context_text: string; text_status: string } | null;
  footnotes: Record<string, string>;
  decisions: ReviewDecision[];
};

export type ReviewItem = {
  target_type: string;
  target_id: string;
  document_id: string;
  kind: string;
  text: string;
  summary?: string;
  citation_label: string | null;
  evidence_chunk_id: string | null;
  review_status: string;
  meta: Record<string, unknown>;
  last_decision: ReviewDecision | null;
};

export type AdminDocument = {
  document_id: string;
  document_code: string;
  document_type: string;
  title: string;
  domain: string;
  text_status: string;
  ingest_scope: string;
  grammar: string | null;
  version: { file_name: string; format: string; sha256: string; size_bytes: number; page_count: number | null; registered_at: string } | null;
  counts: Record<string, number>;
  review_by_status: Record<string, number>;
  last_ingestion: {
    id: number;
    loaded_at: string;
    file_hash: string;
    report_summary: { coverage_ratio: number | null; rules: number | null; chunks: number | null; warnings: number | null; unresolved_entities: number | null };
  } | null;
  in_active_index: boolean;
  stale: boolean;
  not_indexed: boolean;
};

export type IndexVersion = {
  id: number;
  label: string;
  status: string;
  is_active: boolean;
  embedding_model: string;
  embedding_dim: number;
  document_hashes: Record<string, string>;
  notes: string | null;
  created_at: string;
  activated_at: string | null;
};

export type IngestionRun = { id: number; loaded_at: string; file_hash: string; report: Record<string, unknown> };

export const admin = {
  ping: () => adminFetch<{ ok: boolean; version: string }>("/api/v1/admin/ping"),
  health: (probe = false) => adminFetch<HealthDetails>(`/api/v1/admin/health?probe=${probe}`),
  metrics: (window: number) => adminFetch<Metrics>(`/api/v1/admin/metrics?window=${window}`),
  alerts: (active = true) => adminFetch<AlertEvent[]>(`/api/v1/admin/alerts?active=${active}`),
  resolveAlert: (id: number, actor: string) =>
    adminFetch<AlertEvent>(`/api/v1/admin/alerts/${id}/resolve`, { method: "POST", body: JSON.stringify({ actor }) }),
  testAlert: () => adminFetch<{ sent: boolean; error: string | null }>("/api/v1/admin/alerts/test", { method: "POST" }),
  queryLogs: (params: URLSearchParams) => adminFetch<QueryLogList>(`/api/v1/admin/query-logs?${params.toString()}`),
  queryLog: (id: string) => adminFetch<QueryLogDetail>(`/api/v1/admin/query-logs/${encodeURIComponent(id)}`),
  exportCsv: (params: URLSearchParams) =>
    adminDownload(`/api/v1/admin/query-logs/export.csv?${params.toString()}`, `query-logs-${new Date().toISOString().slice(0, 10)}.csv`),
  reviewSummary: () => adminFetch<ReviewSummary>("/api/v1/admin/review/summary"),
  reviewTable: (code: string, params: URLSearchParams) =>
    adminFetch<ReviewTable>(`/api/v1/admin/review/tables/${encodeURIComponent(code)}?${params.toString()}`),
  reviewRow: (rowId: string) => adminFetch<ReviewRowDetail>(`/api/v1/admin/review/rows/${encodeURIComponent(rowId)}`),
  decide: (body: { target_type: string; target_id: string; decision: string; reviewer: string; note?: string; correction?: string }) =>
    adminFetch<{ decision_id: number; review_status: string; rules_updated: number }>("/api/v1/admin/review/decisions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  bulkApprove: (code: string, body: { reviewer: string; note: string }) =>
    adminFetch<{ table: string; sample: number; required: number; rules: number; approved_now: number }>(
      `/api/v1/admin/review/tables/${encodeURIComponent(code)}/bulk-approve`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  reviewItems: (type: string, status?: string) =>
    adminFetch<ReviewItem[]>(`/api/v1/admin/review/items?type=${type}${status ? `&status=${status}` : ""}`),
  documents: (includeStubs = false) => adminFetch<AdminDocument[]>(`/api/v1/admin/documents?include_stubs=${includeStubs}`),
  ingestionRuns: (id: string) => adminFetch<IngestionRun[]>(`/api/v1/admin/documents/${encodeURIComponent(id)}/ingestion-runs`),
  indexVersions: () => adminFetch<IndexVersion[]>("/api/v1/admin/index-versions"),
  activateIndex: (label: string, actor: string) =>
    adminFetch<{ activated: string; previous: string | null }>(`/api/v1/admin/index-versions/${encodeURIComponent(label)}/activate`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),
  stats: () => adminFetch<Record<string, number>>("/api/v1/admin/stats"),
};
