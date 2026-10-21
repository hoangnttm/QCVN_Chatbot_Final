/** Map transport / API errors to a short Vietnamese title + hint for the chat UI (M20). */

import { ApiError } from "@/lib/api";

export type FriendlyError = { title: string; hint: string; requestId?: string; code?: string };

export function friendlyError(err: unknown): FriendlyError {
  if (err instanceof DOMException && err.name === "AbortError") {
    return { title: "Đã dừng theo yêu cầu", hint: "Bạn có thể đặt lại câu hỏi bất cứ lúc nào.", code: "aborted" };
  }
  if (err instanceof ApiError) {
    const code = err.code ?? (err.status >= 500 ? "internal_error" : "request_error");
    switch (code) {
      case "llm_error":
        return {
          title: "Dịch vụ ngôn ngữ đang gặp sự cố",
          hint: "Gemini không phản hồi. Vui lòng thử lại sau ít phút; câu hỏi đã được ghi nhận.",
          requestId: err.requestId,
          code,
        };
      case "internal_error":
        return {
          title: "Có lỗi không mong muốn",
          hint: `Hãy thử lại. Nếu lặp lại, báo cho quản trị viên kèm mã ${err.requestId ?? err.queryId ?? "(không có)"}.`,
          requestId: err.requestId,
          code,
        };
      default:
        if (err.status === 422) {
          return { title: "Câu hỏi chưa hợp lệ", hint: "Câu hỏi cần từ 3 đến 2.000 ký tự.", code };
        }
        if (err.status === 404) {
          return { title: "Không tìm thấy", hint: "Bản ghi không tồn tại hoặc không thuộc trình duyệt này.", code };
        }
        return {
          title: `Máy chủ trả về lỗi ${err.status}`,
          hint: err.detail || "Vui lòng thử lại.",
          requestId: err.requestId,
          code,
        };
    }
  }
  if (err instanceof TypeError) {
    return {
      title: "Không kết nối được máy chủ",
      hint: "Kiểm tra mạng hoặc địa chỉ API (NEXT_PUBLIC_API_URL) rồi thử lại.",
      code: "network",
    };
  }
  return { title: "Có lỗi không mong muốn", hint: String(err), code: "unknown" };
}

/** Message for an SSE `error` event (the stream itself succeeded, the pipeline failed). */
export function friendlyStreamError(data: { code?: string; message?: string; query_id?: string }): FriendlyError {
  if (data.code === "llm_error") {
    return {
      title: "Dịch vụ ngôn ngữ đang gặp sự cố",
      hint: "Gemini không phản hồi. Vui lòng thử lại sau ít phút; câu hỏi đã được ghi nhận.",
      requestId: data.query_id,
      code: data.code,
    };
  }
  return {
    title: "Không xử lý được câu hỏi",
    hint: `Hãy thử lại hoặc diễn đạt lại. Mã tra cứu: ${data.query_id ?? "(không có)"}.`,
    requestId: data.query_id,
    code: data.code ?? "internal_error",
  };
}
