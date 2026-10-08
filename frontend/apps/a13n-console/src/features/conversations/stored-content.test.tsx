// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { DisplayItem } from "a13n-ui/display";
import { createClient } from "../../service-client";
import {
  StoredContent,
  StoredContents,
  LegacyTruncation,
} from "./stored-content";

let requests: Request[] = [];
let respond: (request: Request) => Response | Promise<Response>;
const client = createClient({
  baseUrl: "http://service.test",
  auth: { type: "session" },
  maxReadRetries: 0,
  fetch: async (request) => {
    requests.push(request as Request);
    return respond(request as Request);
  },
});
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: vi.fn(),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
}));
afterEach(() => {
  cleanup();
  requests = [];
});

const item = (id = "cnt_one"): DisplayItem => ({
  id: "itm_one",
  ordinal: 1,
  kind: "text_message",
  state: "completed",
  first_stream_id: "1-1",
  last_stream_id: "1-3",
  started_at: "2026-10-08T00:00:00Z",
  content: { text: "Short preview" },
  content_refs: {
    text: {
      id,
      size_bytes: 50000,
      media_type: "text/plain",
      preview: "Short preview",
    },
  },
});
const view = (value = item()) => (
  <StoredContents runId="run_one" items={[value]}>
    <StoredContent itemId="itm_one" field="text" value="Short preview" />
    <LegacyTruncation itemId="itm_one" />
  </StoredContents>
);

it("loads only on expansion and replaces the preview with the authorized full value", async () => {
  respond = () =>
    Response.json({
      id: "cnt_one",
      media_type: "text/plain",
      value: "The full saved answer.",
    });
  render(view());
  expect(requests).toHaveLength(0);
  expect(screen.getByText("Short preview")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Expand full content" }));
  expect(await screen.findByText("The full saved answer.")).toBeTruthy();
  expect(screen.queryByText("Short preview")).toBeNull();
  expect(requests).toHaveLength(1);
  expect(new URL(requests[0]!.url).pathname).toBe(
    "/api/v1/runs/run_one/contents/cnt_one",
  );
  expect(requests[0]!.headers.get("x-workspace-id")).toBe("workspace");
});

it("keeps the preview on failure and retries without marking the value complete", async () => {
  respond = () =>
    Response.json(
      {
        error: {
          code: "unavailable",
          message: "Body unavailable",
          details: {},
        },
      },
      { status: 503 },
    );
  render(view());
  fireEvent.click(screen.getByRole("button", { name: "Expand full content" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(await screen.findByText("Body unavailable")).toBeTruthy();
  expect(screen.getByText("Short preview")).toBeTruthy();
  respond = () =>
    Response.json({
      id: "cnt_one",
      media_type: "text/plain",
      value: "Recovered body",
    });
  fireEvent.click(screen.getByRole("button", { name: "Expand full content" }));
  expect(await screen.findByText("Recovered body")).toBeTruthy();
});

it("drops an expanded old version when the committed reference changes", async () => {
  respond = () =>
    Response.json({
      id: "cnt_one",
      media_type: "text/plain",
      value: "Old body",
    });
  const shown = render(view());
  fireEvent.click(screen.getByRole("button", { name: "Expand full content" }));
  await screen.findByText("Old body");
  shown.rerender(view(item("cnt_two")));
  expect(screen.queryByText("Old body")).toBeNull();
  expect(screen.getByText("Short preview")).toBeTruthy();
  expect(requests).toHaveLength(1);
});

it("labels capped content before and after expansion without claiming the missing suffix is available", async () => {
  const capped = item();
  capped.content_refs!.text!.truncated = true;
  respond = () =>
    Response.json({
      id: "cnt_one",
      media_type: "text/plain",
      value: "Saved prefix",
      truncated: true,
    });
  render(view(capped));
  const warning =
    "This content exceeded 16 MiB and was truncated. Only the saved prefix is available.";
  expect(screen.getByText(warning).getAttribute("role")).toBe("status");
  expect(
    screen.queryByRole("button", { name: "Expand full content" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Expand saved content" }));
  expect(await screen.findByText("Saved prefix")).toBeTruthy();
  expect(screen.getByText(warning).getAttribute("role")).toBe("status");
  expect(requests).toHaveLength(1);
});

it("explains unavailable legacy content without inventing a reference", () => {
  const legacy = item();
  delete legacy.content_refs;
  legacy.content.truncated = true;
  render(view(legacy));
  expect(
    screen.getByText(
      "This historical record was truncated. The remaining content is unavailable.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Expand full content" }),
  ).toBeNull();
});
