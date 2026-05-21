// Minimal fetch wrapper. The base URL is empty in dev (Vite proxies
// /api and /health) and configurable via VITE_API_BASE for production.

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export interface ApiError extends Error {
  status: number;
  detail?: unknown;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail: unknown = undefined;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const err = new Error(`API ${response.status} ${path}`) as ApiError;
    err.status = response.status;
    err.detail = detail;
    throw err;
  }
  // 204 No Content path — not used today but cheap to handle.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get<T>(path: string) {
    return request<T>(path);
  },
  post<T>(path: string, body: unknown) {
    return request<T>(path, { method: "POST", body: JSON.stringify(body) });
  },
};
