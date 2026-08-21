/**
 * CakeCRM — API client with JWT auth.
 * Vite proxies /api → localhost:8000 in development.
 */

import { getToken, TOKEN_KEY } from '../auth/tokenUtils';

/**
 * A failed response, carrying the HTTP status so a caller can tell a REFUSAL from an
 * UNKNOWN outcome (issue #55). A 4xx means the request never reached a write, so
 * nothing changed; a 5xx or a transport failure is genuinely ambiguous — the
 * connection can drop after Postgres has committed but before the ack arrives.
 * Reporting those the same way would either manufacture doubt or claim knowledge we
 * do not have.
 *
 * Extends Error, so every existing `catch` that treats it as one keeps working.
 */
export class ApiError extends Error {
  // Declared explicitly rather than as constructor parameter properties: this project
  // compiles with `erasableSyntaxOnly`, which bans that shorthand.
  status: number;
  detail: string;

  constructor(message: string, status: number, detail: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const isFormData = options.body instanceof FormData;
  const headers: Record<string, string> = {
    // Let the browser set multipart/form-data (with boundary) for FormData
    // uploads; default to JSON otherwise.
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    window.location.href = '/login';
    // Never settle: the page is navigating away. Throwing here would run
    // every caller's catch block and flash false "Failed to ..." toasts
    // and error states in the instant before the redirect commits.
    return new Promise<never>(() => {});
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      // Only when it's actually a string. FastAPI sends an ARRAY for a 422 (and an object
      // for some gates), and assigning that through would make `ApiError.detail` lie about
      // its type — a caller calling .trim() on it would throw instead of rendering a reason.
      if (typeof body.detail === 'string' && body.detail) detail = body.detail;
    } catch { /* not JSON */ }
    throw new ApiError(`API error ${res.status}: ${detail}`, res.status, detail);
  }

  return res.json();
}
