import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createApiClient,
  createCSVDownloader,
  getSessionToken,
  queryString,
} from "../api/client";

const token = vi.fn<() => Promise<string>>();
const fetchMock = vi.fn<typeof fetch>();
const request = createApiClient(token);
const json = (value: unknown, status = 200, headers = {}) =>
  new Response(JSON.stringify(value), { status, headers });
beforeEach(() => {
  token.mockReset().mockResolvedValue("dummy-session");
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("CSV download client", () => {
  it("uses Bearer fetch and returns a Blob without cookies", async () => {
    fetchMock.mockResolvedValue(new Response("\ufeffid,points\r\n1,10", {headers:{"Content-Type":"text/csv; charset=utf-8"}}));
    const blob=await createCSVDownloader(token)("ledger",{from:"2026-10-01",include_pii:"0"});
    expect(blob).toBeInstanceOf(Blob);
    expect(fetchMock).toHaveBeenCalledWith("/api/admin/reports/export/ledger?from=2026-10-01&include_pii=0",expect.objectContaining({credentials:"omit",redirect:"error",headers:{Authorization:"Bearer dummy-session",Accept:"*/*"}}));
  });
  it("preserves EXPORT_TOO_LARGE and refreshes a rejected session once", async () => {
    fetchMock.mockResolvedValueOnce(json({},401,{"X-Shopify-Retry-Invalid-Session-Request":"1"}))
      .mockResolvedValueOnce(json({code:"EXPORT_TOO_LARGE",message:"Narrow filters"},422));
    await expect(createCSVDownloader(token)("customers")).rejects.toMatchObject({code:"EXPORT_TOO_LARGE",message:"Narrow filters"});
    expect(token).toHaveBeenCalledTimes(2);
  });
});
describe("API client", () => {
  it("accepts an empty 204 response when deleting a scheduled version", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(request<void>("program-config/3", { method: "DELETE" })).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith("/api/admin/program-config/3", expect.objectContaining({ method: "DELETE" }));
  });
  it("obtains a fresh token for each request and never sends cookies", async () => {
    token.mockResolvedValueOnce("dummy-one").mockResolvedValueOnce("dummy-two");
    fetchMock.mockImplementation(async () => json({ ok: true }));
    await request("me");
    await request("me");
    expect(token).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.map(
        (call) => (call[1]?.headers as Record<string, string>).Authorization,
      ),
    ).toEqual(["Bearer dummy-one", "Bearer dummy-two"]);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: "omit",
      cache: "no-store",
    });
  });
  it("refreshes once on the Shopify retry header, preserving the mutation body", async () => {
    fetchMock
      .mockResolvedValueOnce(
        json({}, 401, { "X-Shopify-Retry-Invalid-Session-Request": "1" }),
      )
      .mockResolvedValueOnce(json({ duplicate: true }));
    await expect(
      request("customers/1/adjust", {
        method: "POST",
        body: { client_key: "dummy-key", delta: 1 },
      }),
    ).resolves.toEqual({ duplicate: true });
    expect(token).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      fetchMock.mock.calls[1]?.[1]?.body,
    );
  });
  it("never retries a second 401 or a 401 without the retry header", async () => {
    fetchMock.mockResolvedValue(
      json({ code: "INVALID_SESSION", message: "Expired" }, 401, {
        "X-Shopify-Retry-Invalid-Session-Request": "1",
      }),
    );
    await expect(request("me")).rejects.toMatchObject({
      status: 401,
      code: "INVALID_SESSION",
      message: "Expired",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fetchMock.mockClear().mockResolvedValue(json({}, 401));
    await expect(request("me")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("preserves a structured API error including details", async () => {
    fetchMock.mockResolvedValue(
      json(
        {
          code: "KEY_REUSED",
          message: "Already used",
          details: { field: "client_key" },
        },
        409,
      ),
    );
    await expect(request("customers/1/adjust")).rejects.toMatchObject({
      status: 409,
      code: "KEY_REUSED",
      message: "Already used",
      details: { field: "client_key" },
    });
  });
  it.each([429, 500, 503])(
    "explains HTTP %s even with a non-JSON body",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("Unavailable", { status }));
      await expect(request("me")).rejects.toMatchObject({
        status,
        message: expect.stringMatching(
          status === 429 ? /Too many requests/ : /temporarily unavailable/,
        ),
      });
    },
  );
  it("maps network failures and rejects successful malformed JSON", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(request("me")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
    });
    fetchMock.mockResolvedValueOnce(new Response("<html>"));
    await expect(request("me")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });
  it("passes cancellation to fetch and keeps AbortError distinct", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) =>
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Cancelled", "AbortError")),
          ),
        ),
    );
    const pending = request("customers", { signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
    await expect(
      request("me", { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("serializes filters with false/zero and safely encodes search text", () => {
    expect(
      queryString({
        page: 2,
        blocked: false,
        zero: 0,
        search: "x & y",
        empty: "",
        none: null,
        missing: undefined,
      }),
    ).toBe("?page=2&blocked=false&zero=0&search=x+%26+y");
  });
  it("allows only the development outside-Shopify override", async () => {
    vi.stubEnv("VITE_DEV_TOKEN", "dummy-development");
    await expect(getSessionToken()).resolves.toBe("dummy-development");
    vi.stubEnv("DEV", false);
    await expect(getSessionToken()).rejects.toMatchObject({
      code: "NOT_EMBEDDED",
    });
  });
  it("calls the mocked App Bridge token function for every embedded request", async () => {
    vi.stubGlobal("window", { top: {}, self: {}, shopify: { idToken: token } });
    fetchMock.mockImplementation(async () => json({ ok: true }));
    const embeddedRequest = createApiClient();
    await embeddedRequest("me");
    await embeddedRequest("me");
    expect(token).toHaveBeenCalledTimes(2);
    token.mockRejectedValueOnce(new Error("Session unavailable"));
    await expect(embeddedRequest("me")).rejects.toMatchObject({
      code: "TOKEN_UNAVAILABLE",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
