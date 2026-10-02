import { ApiClient } from "./client";

describe("ApiClient", () => {
  it("binds the browser fetch implementation to globalThis", async () => {
    const original = globalThis.fetch;
    let receiver: unknown;
    globalThis.fetch = (function (this: unknown) {
      receiver = this;
      return Promise.resolve(new Response(JSON.stringify({ projects: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    }) as typeof fetch;
    try {
      await new ApiClient().request("/api/v1/workspace");
      expect(receiver).toBe(globalThis);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("bootstraps an HttpOnly session and sends CSRF only on mutations", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf-safe" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "project-1" }), { status: 201, headers: { "content-type": "application/json" } }));
    const client = new ApiClient(fetcher);

    await client.request("/api/v1/projects", { method: "POST", body: { name: "Demo" } });

    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/v1/session", expect.objectContaining({ credentials: "include" }));
    expect(fetcher).toHaveBeenNthCalledWith(2, "/api/v1/projects", expect.objectContaining({ credentials: "include" }));
    const mutationInit = fetcher.mock.calls[1][1] as RequestInit;
    expect(new Headers(mutationInit.headers).get("X-CSRF-Token")).toBe("csrf-safe");
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("api_key");
  });

  it("normalizes server failures without leaking response internals", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "冲突" }), { status: 409, headers: { "content-type": "application/json" } }));
    const client = new ApiClient(fetcher);
    await expect(client.request("/api/v1/projects")).rejects.toEqual(expect.objectContaining({ status: 409, message: "冲突" }));
  });

  it("bootstraps and retries a read once after an unauthenticated response", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf-safe" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ projects: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    const client = new ApiClient(fetcher);
    await expect(client.request("/api/v1/workspace")).resolves.toEqual({ projects: [] });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
