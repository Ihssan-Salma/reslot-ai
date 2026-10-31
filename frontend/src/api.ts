export type User = { id: string; email: string; active_tenant_id: string | null };
export type Business = {
  id: string; name: string; business_type: string; timezone: string;
  status: string; role: 'owner' | 'admin' | 'staff'; created_at: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, body?: unknown, method?: 'GET' | 'POST' | 'PUT' | 'DELETE'): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined || (method && method !== 'GET')) {
    const csrfResponse = await fetch('/api/v1/auth/csrf', { credentials: 'include', cache: 'no-store' });
    if (!csrfResponse.ok) throw new Error('Could not prepare a secure request. Please retry.');
    const { csrf_token } = await csrfResponse.json() as { csrf_token: string };
    headers['X-CSRF-Token'] = csrf_token;
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(`/api/v1${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'), credentials: 'include',
    headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store',
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail :
      'Check your entries and try again.');
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
