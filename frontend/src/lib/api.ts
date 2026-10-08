import type {
  ApiErrorShape,
  CreateSessionInput,
  CurrentUser,
  ReviewSession,
  ReviewSessionSummary,
  ReviewStatus,
} from "@/lib/types";

const API_ROOT = "/api/backend";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorShape;
    throw new ApiRequestError(error.detail ?? `Request failed (${response.status})`, response.status);
  }

  return (await response.json()) as T;
}

export function listSessions(filters: { status?: ReviewStatus; search?: string } = {}) {
  const query = new URLSearchParams({ limit: "100" });
  if (filters.status) query.set("status", filters.status);
  if (filters.search?.trim()) query.set("search", filters.search.trim());
  return request<ReviewSessionSummary[]>(`/sessions?${query.toString()}`);
}

export function getCurrentUser() {
  return request<CurrentUser>("/sessions/me");
}

export function getSession(id: string) {
  return request<ReviewSession>(`/sessions/${encodeURIComponent(id)}`);
}

export function createSession(input: CreateSessionInput) {
  return request<ReviewSession>("/sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function editSession(id: string, documentEditId: number, content: string) {
  return request<ReviewSession>(`/sessions/${encodeURIComponent(id)}/edit`, {
    method: "POST",
    body: JSON.stringify({ document_edit_id: documentEditId, content }),
  });
}

export function approveSession(id: string) {
  return request<{ id: string; status: ReviewStatus }>(`/sessions/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });
}

export function rejectSession(id: string, reason?: string) {
  return request<{ id: string; status: ReviewStatus }>(`/sessions/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}