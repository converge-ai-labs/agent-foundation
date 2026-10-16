import { expect, it, vi } from "vitest";
import { ApiError, createClient } from "../../service-client";
import { prepareConversationAgent } from "./prepare";

const agent = {
  source: "builtin" as const,
  preset_kind: "composer" as const,
  default_revision_id: "apr_old",
};

it.each(["composer", "finding"] as const)(
  "prepares the existing %s preset with the caller's workspace credentials",
  async (kind) => {
    const requests: Request[] = [];
    const client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session", csrfToken: "csrf" },
      fetch: async (input, init) => {
        requests.push(new Request(input, init));
        return Response.json({
          id: "ap_builtin",
          default_revision_id: "apr_new",
        });
      },
    });
    try {
      expect(
        await prepareConversationAgent(
          client,
          "ws_test",
          { ...agent, preset_kind: kind },
          true,
        ),
      ).toBe(true);
      expect(requests).toHaveLength(1);
      expect(new URL(requests[0]!.url).pathname).toBe(
        kind === "composer"
          ? "/api/v1/agent-composer"
          : "/api/v1/finding-agent",
      );
      expect(requests[0]!.method).toBe("POST");
      expect(requests[0]!.headers.get("X-Workspace-ID")).toBe("ws_test");
      expect(requests[0]!.headers.get("X-CSRF-Token")).toBe("csrf");
    } finally {
      client.close();
    }
  },
);

it.each([
  { name: "runner without write", value: agent, write: false },
  {
    name: "custom agent",
    value: { ...agent, source: "custom" as const, preset_kind: null },
    write: true,
  },
  {
    name: "memory organizer",
    value: { ...agent, preset_kind: "memory_organizer" as const },
    write: true,
  },
])(
  "keeps the saved configuration for $name without a preparation request",
  async ({ value, write }) => {
    const fetch = vi.fn();
    const client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session", csrfToken: "csrf" },
      fetch,
    });
    try {
      expect(
        await prepareConversationAgent(client, "ws_test", value, write),
      ).toBe(false);
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      client.close();
    }
  },
);

it.each([
  { status: 403, code: "forbidden", revision: "apr_old", fallback: true },
  { status: 403, code: "forbidden", revision: null, fallback: false },
  { status: 403, code: "csrf_invalid", revision: "apr_old", fallback: false },
  {
    status: 401,
    code: "unauthenticated",
    revision: "apr_old",
    fallback: false,
  },
  { status: 409, code: "conflict", revision: "apr_old", fallback: false },
  { status: 503, code: "unavailable", revision: "apr_old", fallback: false },
])(
  "handles $status $code with saved revision $revision",
  async ({ status, code, revision, fallback }) => {
    const client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session", csrfToken: "csrf" },
      fetch: async () =>
        Response.json(
          { error: { code, message: "Preparation failed" } },
          { status },
        ),
    });
    try {
      const result = prepareConversationAgent(
        client,
        "ws_test",
        { ...agent, default_revision_id: revision },
        true,
      );
      if (fallback) await expect(result).resolves.toBe(false);
      else await expect(result).rejects.toBeInstanceOf(ApiError);
    } finally {
      client.close();
    }
  },
);
