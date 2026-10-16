import { z } from "zod";
import { ResearchReportSchema } from "../src/services/research-contract";

export const BASE = "/api/v1/agents/executions";
export const terminal = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
]);
export const JobSchema = z.object({
  execution_id: z.string().uuid(),
  session_id: z.string().uuid(),
  query: z.string().optional(),
  state: z.enum([
    "QUEUED",
    "RUNNING",
    "CANCEL_REQUESTED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
  ]),
  created_at: z.string(),
  updated_at: z.string(),
  completed_at: z.string().optional(),
  started_at: z.string().optional(),
  current_phase: z.string().optional(),
  model_version: z.string(),
  attempt_count: z.string().optional(),
  generation: z.string().optional(),
  failure_code: z.string().optional(),
  retry_execution_id: z.string().uuid().optional(),
  parent_execution_id: z.string().optional(),
  result: ResearchReportSchema.optional(),
  request_options: z.record(z.string(), z.unknown()).optional(),
});
export type Job = z.infer<typeof JobSchema>;
export type Scope = {
  category: "company" | "comparison" | "historical_prices" | "general";
  symbols: string[];
  start_date?: string;
  end_date?: string;
};
export const ModelSchema = z.object({
  protocol_version: z.literal(1),
  provider: z.string(),
  model: z.string(),
  configured: z.boolean(),
});
export type Model = z.infer<typeof ModelSchema>;
const HistorySchema = z.object({
  executions: z.array(JobSchema),
  next_cursor: z.string().uuid().nullable(),
});
const AdmissionSchema = z.object({ execution_id: z.string().uuid() });

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export class ResearchApi {
  constructor(private token: string) {
    if (
      typeof location !== "undefined" &&
      location.protocol !== "https:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)
    ) {
      throw new Error("Use HTTPS for a remote research workspace.");
    }
  }
  async request(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    const response = await fetch(path, {
      ...init,
      headers,
      credentials: "omit",
      cache: "no-store",
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as {
        detail?: unknown;
      };
      const detail =
        typeof body.detail === "string"
          ? body.detail
          : response.status === 422
            ? "Check the research question, symbols and dates."
            : `Request failed (${response.status}).`;
      throw new ApiError(response.status, detail);
    }
    return response;
  }
  async model(signal?: AbortSignal) {
    return ModelSchema.parse(
      await (await this.request("/api/v1/model", { signal })).json(),
    );
  }
  async history(before?: string, signal?: AbortSignal) {
    return HistorySchema.parse(
      await (
        await this.request(
          `${BASE}?limit=20${before ? `&cursor=${encodeURIComponent(before)}` : ""}`,
          { signal },
        )
      ).json(),
    );
  }
  async job(id: string, signal?: AbortSignal) {
    return JobSchema.parse(
      await (await this.request(`${BASE}/${id}`, { signal })).json(),
    );
  }
  async submit(query: string, scope: Scope, key: string, signal?: AbortSignal) {
    return AdmissionSchema.parse(
      await (
        await this.request(BASE, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
          },
          body: JSON.stringify({ query, scope }),
          signal,
        })
      ).json(),
    );
  }
  async cancel(id: string, signal?: AbortSignal) {
    await this.request(`${BASE}/${id}/cancel`, { method: "POST", signal });
  }
  async retry(id: string, key: string, signal?: AbortSignal) {
    return AdmissionSchema.parse(
      await (
        await this.request(`${BASE}/${id}/retry`, {
          method: "POST",
          headers: { "Idempotency-Key": key },
          signal,
        })
      ).json(),
    );
  }
  async watch(
    id: string,
    onEvent: () => void,
    signal: AbortSignal,
    lastId: { value: string },
  ) {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (lastId.value) headers["Last-Event-ID"] = lastId.value;
    const response = await this.request(`${BASE}/${id}/events`, {
      headers,
      signal,
    });
    const reader = response.body?.getReader();
    if (!reader)
      throw new Error("Streaming unavailable; using status polling.");
    let buffer = "";
    const decoder = new TextDecoder();
    try {
      while (!signal.aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder
          .decode(value, { stream: true })
          .replace(/\r\n/g, "\n");
        if (buffer.length > 2_000_000)
          throw new Error("Progress frame exceeded size limit.");
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const eventId = frame
            .split("\n")
            .find((line) => line.startsWith("id:"))
            ?.slice(3)
            .trim();
          if (eventId && /^\d+-\d+$/.test(eventId)) lastId.value = eventId;
          if (frame.includes("data:")) onEvent(); // Fetch committed truth; never infer completion from prose.
        }
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
}

export function safeUrl(value: string | null | undefined) {
  try {
    const url = new URL(value || "");
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function pretty(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
export function timestamp(value?: string) {
  if (!value) return "Unavailable";
  const date = new Date(
    /^\d+(\.\d+)?$/.test(value) ? Number(value) * 1000 : value,
  );
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
}
