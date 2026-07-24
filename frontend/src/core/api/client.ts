/**
 * CakeCRM — API client with JWT auth.
 * Vite proxies /api → localhost:8000 in development.
 */

import { getToken, TOKEN_KEY } from '../auth/tokenUtils';

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
      if (body.detail) detail = body.detail;
    } catch { /* not JSON */ }
    throw new Error(`API error ${res.status}: ${detail}`);
  }

  return res.json();
}
