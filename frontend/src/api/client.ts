import type { ApiFailure, Query } from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isEmbedded(): boolean {
  return window.top !== window.self;
}

export async function getSessionToken(): Promise<string> {
  if (!isEmbedded()) {
    if (import.meta.env.DEV && import.meta.env.VITE_DEV_TOKEN)
      return import.meta.env.VITE_DEV_TOKEN;
    throw new ApiError(
      401,
      "NOT_EMBEDDED",
      "Open this app from Shopify admin to sign in.",
    );
  }
  if (!window.shopify?.idToken)
    throw new ApiError(
      401,
      "BRIDGE_UNAVAILABLE",
      "Shopify App Bridge is unavailable. Reopen the app from Shopify admin.",
    );
  try {
    return await window.shopify.idToken();
  } catch {
    throw new ApiError(
      401,
      "TOKEN_UNAVAILABLE",
      "Could not refresh your Shopify session. Reopen the app.",
    );
  }
}

export function queryString(query: Query = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "")
      params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function isFailure(value: unknown): value is ApiFailure {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    typeof value.code === "string" &&
    "message" in value &&
    typeof value.message === "string"
  );
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
}

export function createApiClient(
  tokenProvider: () => Promise<string> = getSessionToken,
) {
  return async function request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const url = `/api/admin/${path}${queryString(options.query)}`;
    const body =
      options.body === undefined ? undefined : JSON.stringify(options.body);
    for (let attempt = 0; attempt < 2; attempt++) {
      options.signal?.throwIfAborted();
      const token = await tokenProvider();
      options.signal?.throwIfAborted();
      let response: Response;
      try {
        response = await fetch(url, {
          method: options.method ?? "GET",
          body,
          signal: options.signal,
          credentials: "omit",
          cache: "no-store",
          redirect: "error",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError")
          throw error;
        if (options.signal?.aborted)
          throw new DOMException("Request cancelled", "AbortError");
        throw new ApiError(
          0,
          "NETWORK_ERROR",
          "Could not connect. Check your connection, then retry.",
        );
      }
      if (
        response.status === 401 &&
        attempt === 0 &&
        response.headers.get("X-Shopify-Retry-Invalid-Session-Request") === "1"
      )
        continue;
      if (response.ok && response.status === 204) return undefined as T;
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        options.signal?.throwIfAborted();
        payload = undefined;
      }
      if (!response.ok) {
        const fallback =
          response.status === 429
            ? "Too many requests. Wait a moment, then retry."
            : response.status >= 500
              ? "The service is temporarily unavailable. Please retry shortly."
              : "The request could not be completed.";
        throw new ApiError(
          response.status,
          isFailure(payload) ? payload.code : `HTTP_${response.status}`,
          isFailure(payload) ? payload.message : fallback,
          isFailure(payload) ? payload.details : undefined,
        );
      }
      if (payload === undefined)
        throw new ApiError(
          response.status,
          "INVALID_RESPONSE",
          "The server returned an unreadable response.",
        );
      return payload as T;
    }
    throw new ApiError(
      401,
      "INVALID_SESSION",
      "Your session could not be refreshed. Reopen the app.",
    );
  };
}

export const apiRequest = createApiClient();

export function createCSVDownloader(tokenProvider = getSessionToken) {
  return async (kind: string, query: Query = {}, signal?: AbortSignal): Promise<Blob> => {
    if (!["customers", "ledger", "lots", "redemptions"].includes(kind))
      throw new ApiError(422, "INVALID_EXPORT", "Unknown export type.");
    for (let attempt = 0; attempt < 2; attempt++) {
      signal?.throwIfAborted();
      const token = await tokenProvider();
      let response: Response;
      try {
        response = await fetch(`/api/admin/reports/export/${kind}${queryString(query)}`, {
          signal, credentials: "omit", cache: "no-store", redirect: "error",
          headers: {Authorization: `Bearer ${token}`, Accept: "*/*"},
        });
      } catch (error) {
        if (signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
        throw new ApiError(0, "NETWORK_ERROR", "Could not download. Check your connection and retry.");
      }
      if (response.status === 401 && attempt === 0 && response.headers.get("X-Shopify-Retry-Invalid-Session-Request") === "1") continue;
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => undefined);
        throw new ApiError(response.status, isFailure(payload) ? payload.code : `HTTP_${response.status}`, isFailure(payload) ? payload.message : "Could not export. Narrow your filters and retry.");
      }
      if (!response.headers.get("Content-Type")?.startsWith("text/csv"))
        throw new ApiError(502, "INVALID_RESPONSE", "Expected a CSV file.");
      return response.blob();
    }
    throw new ApiError(401, "INVALID_SESSION", "Reopen the app to refresh your session.");
  };
}
export const downloadCSV = createCSVDownloader();

export async function downloadCustomerData(profileId: number, signal?: AbortSignal): Promise<Blob> {
  const data = await apiRequest<Record<string, unknown>>(`customers/${profileId}/data-export`, {signal});
  return new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
}

export function asApiError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(
        0,
        "UNEXPECTED_ERROR",
        "Something went wrong. Please retry.",
      );
}
