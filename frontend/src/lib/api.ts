/** Typed client for the read-only document API (backend/app/api/documents.py). */

import { clientHeaders } from "@/lib/client-id";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Error raised for non-2xx responses: status, optional API `code`, request/query ids. */
export class ApiError extends Error {
  status: number;
  code?: string;
  detail: string;
  requestId?: string;
  queryId?: string;
  path?: string;
  detailObject?: unknown;
  constructor(
    status: number,
    message: string,
    opts: { code?: string; requestId?: string; queryId?: string; path?: string; detailObject?: unknown } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = message;
    this.code = opts.code;
    this.requestId = opts.requestId;
    this.queryId = opts.queryId;
    this.path = opts.path;
    this.detailObject = opts.detailObject;
  }
}

export async function toApiError(res: Response, method: string, path: string): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* no JSON body */
  }
  const d = (body as { detail?: unknown } | null)?.detail ?? body;
  const obj = typeof d === "object" && d !== null ? (d as Record<string, unknown>) : null;
  const code = obj && typeof obj.code === "string" ? obj.code : undefined;
  const queryId = obj && typeof obj.query_id === "string" ? obj.query_id : undefined;
  const message =
    typeof d === "string"
      ? d
      : obj && typeof obj.message === "string"
        ? obj.message
        : `${res.status} ${res.statusText} — ${method} ${path}`;
  return new ApiError(res.status, message, {
    code,
    queryId,
    requestId: res.headers.get("x-request-id") ?? undefined,
    path,
    detailObject: d,
  });
}

export type RegistryFact = {
  fact_type: string;
  value: string;
  source: "from_text" | "admin_declared";
  evidence: string | null;
  scope: string | null;
  declared_by: string | null;
  declared_at: string | null;
};

export type CorpusInfo = {
  domains: { code: string; name: string }[];
  documents: {
    document_id: string;
    document_code: string;
    short_code: string;
    document_type: string;
    domain: string;
  }[];
  sample_questions: string[];
};

export type DocumentSummary = {
  document_id: string;
  document_code: string;
  document_type: string;
  title: string;
  issuer: string | null;
  domain: string;
  text_status: "official" | "draft" | "unverified";
  ingest_scope: string;
  note: string | null;
  version: {
    file_name: string;
    format: string;
    sha256: string;
    page_count: number | null;
    registered_at: string;
  } | null;
  facts: RegistryFact[];
  chunks?: number;
};

export type Relation = {
  direction: "in" | "out";
  type: string;
  source: string;
  target: string | null;
  target_document_id: string | null;
  scope: string;
  target_part: string | null;
  condition: string | null;
  origin: "registry" | "text";
  evidence: string | null;
  citation_label: string | null;
};

export type TransitionRule = {
  rule_id: string;
  kind: string;
  text: string;
  dates: string[];
  relative_dates: string[];
  citation_label: string;
};

export type DocumentDetail = DocumentSummary & {
  relations: Relation[];
  transition_rules: TransitionRule[];
  counts: Record<string, number>;
  review: Record<string, number>;
  tables: { table_code: string; title: string | null; node_id: string; columns: string[] }[];
};

export type TreeNode = {
  node_id: string;
  kind: string;
  number: string | null;
  label: string | null;
  title: string | null;
  term: string | null;
  text?: string | null;
  citation_label: string | null;
  scope: string;
  domain: string | null;
  span: { line_start: number; line_end: number; pdf_page_start: number | null; printed_page: string | null };
  table_code: string | null;
  children: TreeNode[];
};

export type Value = { op: string; value: number | null; min: number | null; max: number | null; ref_o2: number | null; raw: string };
export type Condition = { type: string; op: string | null; value: unknown; unit: string | null; text: string | null; inherited_from: string | null };

export type TableRow = {
  row_id: string;
  row_no: string;
  row_path: string[];
  is_group_header: boolean;
  inherits_from: string | null;
  cells: Record<string, string>;
  raw_cells: Record<string, string>;
  entities: Record<string, unknown>;
  values: Record<string, Value>;
  conditions: Condition[];
  exceptions: string[];
  methods: { code: string; order: number; stage: string }[];
  conditional_parameters: { condition_text?: string; parameters_raw?: string; parameter_ids?: string[] }[];
  vsic_codes: string[];
  coefficient: { name?: string; tef?: number } | null;
  footnote_refs: string[];
  citation_label: string;
  parse_warnings: string[];
  span: { line_start: number; printed_page: string | null };
  rules: { rule_code: string; zone: string; review_status: string; citation_label: string }[];
};

export type TableDetail = {
  table_code: string;
  document_id: string;
  title: string | null;
  citation_label: string | null;
  columns: string[];
  header_rows: string[][];
  warnings: string[];
  rows: TableRow[];
  footnotes: Record<string, string | null>;
};

export type MonitoringRequirement = {
  requirement_id: string;
  requirement_type: string;
  domain: string;
  summary: string;
  text: string;
  conditions: Condition[];
  parameters: string[];
  references: string[];
  citation_label: string;
  evidence_chunk_id: string;
  review_status: string;
};

async function get<T>(path: string, headers: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
    headers: { ...clientHeaders(), ...headers },
  });
  if (!res.ok) throw await toApiError(res, "GET", path);
  return (await res.json()) as T;
}

export const api = {
  getCorpus: () => get<CorpusInfo>("/api/v1/corpus"),
  listDocuments: () => get<DocumentSummary[]>("/api/v1/documents"),
  getDocument: (id: string) => get<DocumentDetail>(`/api/v1/documents/${encodeURIComponent(id)}`),
  getTree: (id: string) =>
    get<{ document_id: string; nodes: number; tree: TreeNode[] }>(
      `/api/v1/documents/${encodeURIComponent(id)}/tree?include_text=true`,
    ),
  getTable: (code: string) => get<TableDetail>(`/api/v1/tables/${encodeURIComponent(code)}`),
  getMonitoring: (id: string) =>
    get<MonitoringRequirement[]>(`/api/v1/documents/${encodeURIComponent(id)}/monitoring`),
};

export const STATUS_LABEL: Record<string, string> = {
  official: "Bản chính thức",
  draft: "Bản dự thảo",
  unverified: "Chưa đối chiếu bản ký",
};

export const DOMAIN_LABEL: Record<string, string> = {
  wastewater: "Nước thải",
  emission: "Khí thải",
  both: "Nước thải + khí thải",
  other: "Khác",
  ambient: "Môi trường nền",
  pop: "POP",
};

export const FACT_LABEL: Record<string, string> = {
  document_number: "Số hiệu",
  issue_date: "Ngày ký",
  signing_place: "Nơi ký",
  effective_date: "Ngày hiệu lực",
  partial_effective_date: "Hiệu lực một phần",
  signer: "Người ký",
};

// ---------------------------------------------------------------- QA (Phase 4)

export type EvidenceItem = {
  rank: number;
  chunk_id: string;
  citation_label: string;
  document_id: string;
  document_code: string;
  chunk_type: string;
  domain: string;
  text_status: string;
  score: number;
  sources: string[];
  text: string;
};

export type StructuredHit = {
  rule_code: string;
  row_id: string;
  chunk_id: string;
  document_id: string;
  table_code: string;
  parameter_id: string | null;
  parameter_raw: string;
  zone: string | null;
  op: string;
  value: number | null;
  min: number | null;
  max: number | null;
  unit_raw: string;
  ref_o2: number | null;
  conditions: string[];
  exceptions: string[];
  citation_label: string;
  review_status: string;
};

export type Understanding = {
  question: string;
  domain: string;
  intent: string;
  parameters: string[];
  zone: string | null;
  flow_m3_day: number | null;
  fuel_type: string | null;
  capacity_value: number | null;
  capacity_unit: string | null;
  receiving_body: string | null;
  sector_id: string | null;
  document_ids: string[];
  as_of_date: string | null;
  policy: string | null;
  needs_llm: boolean;
};

export type Basis = { text: string; citations: string[] };

export type Answer = {
  conclusion: string;
  basis: Basis[];
  conditions: string[];
  citations: string[];
  missing_information: string[];
  warnings: string[];
  abstained: boolean;
  abstain_reason: string | null;
};

export type Citation = { chunk_id: string; citation_label: string; document_code: string; text_status: string };

export type QAResponse = {
  query_id: string;
  question: string;
  as_of_date: string | null;
  understanding: Understanding;
  structured_hits: StructuredHit[];
  evidence: EvidenceItem[];
  answer: Answer;
  citations: Citation[];
  validation: {
    ok: boolean;
    errors: string[];
    warnings: string[];
    rules_checked: string[];
    graph?: GraphCheck | null;
  };
  /** rule 8: graph path behind each citation (history/log: under validation.graph.paths) */
  evidence_paths?: EvidencePath[];
  status: "answered" | "abstained" | "failed";
  path: "policy" | "llm" | "fallback";
  attempts: number;
  model: string | null;
  thinking_level: string | null;
  prompt_version: string;
  usage: Record<string, number>;
  cost_usd: number;
  latency_ms: Record<string, number>;
  disclaimer: string;
};

export type PathNode = {
  id: string;
  kind: string;
  number?: string | null;
  title?: string | null;
  citation_label?: string | null;
  printed_page?: string | null;
};

/** Validator rule 8 (graph-grounded citations, backend app/qa/graph_verify.py). */
export type GraphPathStatus = "connected" | "weak" | "contradicted" | "extra_sibling" | "unchecked";

export type GraphPathNode = { id?: string | null; label?: string | null; name?: string | null };

export type EvidencePath = {
  chunk_id: string;
  citation_label: string;
  status: GraphPathStatus;
  reason?: string | null;
  hops?: number | null;
  nodes: GraphPathNode[];
  rels: string[];
  kept_rules: string[];
  allowed_values: string[];
  sibling_values: string[];
};

export type GraphCheck = {
  mode: "off" | "observe" | "enforce";
  ok: boolean;
  skipped?: string | null;
  errors: string[];
  warnings: string[];
  anchors?: string[];
  latency_ms?: number;
  paths?: EvidencePath[];
};

/** Rule 8 paths of an answer: live responses carry `evidence_paths`, logged ones `validation.graph.paths`. */
export function graphPathsOf(r?: { evidence_paths?: EvidencePath[]; validation?: unknown } | null): EvidencePath[] {
  if (!r) return [];
  if (r.evidence_paths?.length) return r.evidence_paths;
  const g = (r.validation as { graph?: GraphCheck | null } | undefined)?.graph;
  return g?.paths ?? [];
}

export type QAEvent =
  | { event: "stage"; data: { stage: string; query_id?: string; model?: string; ok?: boolean; errors?: string[] } }
  | { event: "understanding"; data: { understanding: Understanding } }
  | { event: "evidence"; data: { evidence: EvidenceItem[]; structured_hits: StructuredHit[]; warnings: string[] } }
  | { event: "delta"; data: { text: string } }
  | { event: "answer"; data: QAResponse }
  | { event: "error"; data: { code?: string; query_id?: string; message: string } }
  | { event: "done"; data: Record<string, never> };

/** POST /questions/ask and dispatch each server-sent event (the body is a stream, so no EventSource). */
export async function askStream(
  body: { question: string; as_of_date?: string | null; k?: number },
  onEvent: (e: QAEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/questions/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...clientHeaders() },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await toApiError(res, "POST", "/api/v1/questions/ask");
  if (!res.body) throw new ApiError(res.status, "Máy chủ không trả về dữ liệu", { path: "/api/v1/questions/ask" });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (dataLines.length) {
        onEvent({ event, data: JSON.parse(dataLines.join("\n")) } as QAEvent);
      }
    }
  }
}

export async function sendFeedback(query_id: string, rating: 1 | -1, comment?: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/v1/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...clientHeaders() },
    body: JSON.stringify({ query_id, rating, comment: comment ?? null }),
  });
  if (!res.ok) throw await toApiError(res, "POST", "/api/v1/feedback");
}

export type HistoryItem = {
  query_id: string;
  question: string;
  status: "answered" | "abstained" | "failed" | "error";
  abstained: boolean | null;
  created_at: string;
  conclusion: string | null;
};

/** A stored answer rebuilt from the audit tables (GET /questions/{id}). */
export type StoredQuestion = {
  query_id: string;
  question: string;
  as_of_date: string | null;
  understanding: Understanding;
  structured_hits: { rule_code: string }[];
  evidence: EvidenceItem[];
  answer: Answer | null;
  citations: Citation[];
  validation: QAResponse["validation"] | Record<string, never>;
  status: HistoryItem["status"];
  path: "policy" | "llm" | "fallback" | "error";
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
  mine: boolean;
};

export type QuotaStatus = {
  day: string;
  limit: number;
  used: number;
  remaining: number;
  over_quota: boolean;
  budget: { limit_usd: number; spent_usd: number; ratio: number };
  enforced: boolean;
};

export const getHistory = (limit = 30) => get<HistoryItem[]>(`/api/v1/history?limit=${limit}`);
export const getQuestion = (id: string) => get<StoredQuestion>(`/api/v1/questions/${encodeURIComponent(id)}`);
export const getQuota = () => get<QuotaStatus>("/api/v1/me/quota");

export const getEvidencePath = (chunkId: string) =>
  get<PathNode[]>(`/api/v1/graph/evidence-path/${encodeURIComponent(chunkId)}`);

export const INTENT_LABEL: Record<string, string> = {
  threshold: "Ngưỡng",
  method: "Phương pháp",
  scope: "Phạm vi",
  definition: "Định nghĩa",
  effectivity: "Hiệu lực",
  applicability: "Áp dụng",
  sampling: "Lấy mẫu",
  frequency: "Tần suất",
  other: "Khác",
};
