// Thin fetch() wrapper. Every error from the backend arrives as
// {"error": {"code", "message", "details"}} and is raised as ApiError.

export class ApiError extends Error {
  constructor(status, body) {
    const err = (body && body.error) || {};
    super(err.message || (status ? `Request failed (HTTP ${status})` : "The server could not be reached."));
    this.status = status;
    this.code = err.code || (status ? `HTTP_${status}` : "NETWORK_ERROR");
    this.details = err.details;
    this.body = body;
  }
}

function buildUrl(path, query) {
  const url = new URL(path, window.location.origin);
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  return url;
}

async function request(method, path, { query, body } = {}) {
  let response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (networkError) {
    throw new ApiError(0, null);
  }
  let data = null;
  try {
    data = await response.json();
  } catch (parseError) {
    data = null;
  }
  if (!response.ok) throw new ApiError(response.status, data);
  return { status: response.status, data };
}

export const api = {
  get: (path, query) => request("GET", path, { query }).then((r) => r.data),
  post: (path, body) => request("POST", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
};
