export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code?: string,
    public readonly details?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  skipSession?: boolean;
};

export class ApiClient {
  private csrfToken: string | null = null;
  private bootstrapPromise: Promise<void> | null = null;
  private readonly fetcher: typeof fetch;

  constructor(fetcher?: typeof fetch) {
    this.fetcher = fetcher ?? globalThis.fetch.bind(globalThis);
  }

  async bootstrap(): Promise<void> {
    if (this.csrfToken) return;
    if (!this.bootstrapPromise) {
      this.bootstrapPromise = this.fetcher("/api/v1/session", {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" }
      }).then(async (response) => {
        if (!response.ok) throw await this.toError(response);
        const value = await response.json() as { csrf_token?: unknown };
        if (typeof value.csrf_token !== "string" || !value.csrf_token) {
          throw new ApiError("无法建立本地安全会话", 503);
        }
        this.csrfToken = value.csrf_token;
      }).finally(() => { this.bootstrapPromise = null; });
    }
    await this.bootstrapPromise;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.perform<T>(path, options, true);
  }

  private async perform<T>(path: string, options: RequestOptions, allowBootstrapRetry: boolean): Promise<T> {
    const method = (options.method ?? "GET").toUpperCase();
    const mutation = !["GET", "HEAD", "OPTIONS"].includes(method);
    if (mutation && !options.skipSession) await this.bootstrap();
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    let body: BodyInit | undefined;
    if (options.body instanceof FormData || options.body instanceof Blob || typeof options.body === "string") {
      body = options.body;
    } else if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(options.body);
    }
    if (mutation && this.csrfToken) headers.set("X-CSRF-Token", this.csrfToken);
    const response = await this.fetcher(path, {
      ...options,
      method,
      body,
      headers,
      credentials: "include"
    });
    if (response.status === 401 && allowBootstrapRetry && !options.skipSession) {
      this.csrfToken = null;
      await this.bootstrap();
      return this.perform<T>(path, options, false);
    }
    if (!response.ok) throw await this.toError(response);
    if (response.status === 204) return undefined as T;
    const contentType = response.headers.get("content-type") ?? "";
    return contentType.includes("application/json")
      ? await response.json() as T
      : await response.blob() as T;
  }

  private async toError(response: Response): Promise<ApiError> {
    let details: unknown;
    try { details = await response.json(); } catch { details = undefined; }
    const detail = details && typeof details === "object" && "detail" in details
      ? (details as { detail: unknown }).detail : undefined;
    const message = typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "message" in detail
        ? String((detail as { message: unknown }).message)
        : `请求失败（${response.status}）`;
    const code = detail && typeof detail === "object" && "code" in detail
      ? String((detail as { code: unknown }).code) : undefined;
    return new ApiError(message, response.status, code, details);
  }
}

export const apiClient = new ApiClient();
