# ADR-0006 — Frontend Next.js 15 + shadcn/ui; auth MVP tối giản

**Trạng thái:** Accepted (PLAN.md D7, D8) · **Ngày:** 2026-09-23

## Context
UI gồm chat có citation click-through, form applicability, trang văn bản (render bảng từ dữ liệu dòng), và 5 trang admin CRUD. RBAC/SSO thuộc roadmap P4.

## Decision
- Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui; gọi backend qua `/api/v1`, SSE cho `/questions/ask`.
- Auth MVP: một tài khoản admin (session cookie, mật khẩu trong env) cho `/admin/*`; public QA ẩn danh có rate limit theo IP.
- Scaffold từ tuần 2 (Phase 1) cùng trang `/documents/[id]`.

## Consequences
- Không có phân quyền dữ liệu cơ sở trong MVP; mọi assessment là ephemeral.
- Máy dev có Node 22 + npm 11.

## Bổ sung 2026-09-23 — scaffold thực tế
- `create-next-app@latest` sinh **Next.js 16.3 (App Router, Turbopack), React 19, Tailwind v4, TypeScript**; `params` của page là `Promise` (phải `await`). Chưa cài shadcn/ui — thêm khi làm chat UI/admin (Phase 4–5); trang văn bản chỉ dùng Tailwind.
- Trang `/documents`, `/documents/[id]`, `/documents/[id]/tables/[code]` là server component gọi API qua `NEXT_PUBLIC_API_URL` (mặc định `http://localhost:8000`), `cache: "no-store"` để luôn phản ánh trạng thái duyệt trong DB. Bảng được dựng từ `table_rows` + `limit_rules` (không phải markdown gốc) theo nguyên tắc 3 của README.
- `frontend/AGENTS.md`/`CLAUDE.md` do `next dev` tự sinh (hướng dẫn cho agent về API Next 16) — giữ trong repo.

## Bổ sung 2026-09-24 — auth tạm cho khu vực admin (PO quyết định)
- `/api/v1/admin/*` (review, logs, documents, index activate, ops) bảo vệ bằng **token dùng chung** `ADMIN_TOKEN` (`.env`) gửi qua header `X-Admin-Token`; `require_admin` là dependency mức router (`hmac.compare_digest`; 503 khi chưa cấu hình, 401 khi sai) nên M9 chỉ thay thân hàm.
- Frontend lưu token trong `localStorage["qcvn.admin_token"]` cho tới khi đăng xuất hoặc gặp 401 — chấp nhận cho nội bộ/staging; M9 thay bằng cookie `HttpOnly` + vai trò.
- Người dùng chat vẫn ẩn danh: `X-Client-Id` (UUID sinh ở trình duyệt, `localStorage["qcvn.client_id"]`) gắn lịch sử và `usage_daily`; khi có tài khoản sẽ ánh xạ `client_id` → user. Hạn mức theo client chỉ cảnh báo, không chặn (không 429) cho tới M10.
- Chưa cài shadcn/ui: UI kit tối thiểu tự viết (`components/ui/{Modal,Toast,Kpi,Drawer}`) bằng Tailwind v4.
