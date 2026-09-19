import { ApiError } from "../../service-client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryContents } from "./contents";
import { memoryApi, memoryKey, type MemoryTarget } from "./api";
import { MemoryRecordEditor } from "./record-editor";
import type { Client } from "../../service-client";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PUT: vi.fn(),
  DELETE: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
const target: MemoryTarget = {
  workspace: "ws_test",
  provider_id: "memprov_one",
  scope: "user",
};
const response = (data: unknown) => ({ data, response: new Response(null) });
const record = (index: number) => ({
  id: `native-${index}`,
  memory: `Memory text ${index}`,
  score: null,
});
let canWrite = true;
let items = Array.from({ length: 41 }, (_, index) => record(index));
let pagination: { next_cursor: string | null } | null = null;
function setup(
  element = <MemoryContents target={target} onEditing={vi.fn()} />,
) {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return {
    cache,
    ...render(
      <QueryClientProvider client={cache}>{element}</QueryClientProvider>,
    ),
  };
}
/** Deletion lives behind the panel overflow menu and its confirmation. */
async function deleteMemory(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Memory actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Delete memory" }),
  );
  const confirm = await screen.findByRole("dialog");
  await user.click(
    within(confirm).getByRole("button", { name: "Delete memory" }),
  );
}
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  canWrite = true;
  items = Array.from({ length: 41 }, (_, index) => record(index));
  pagination = null;
  http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/memory-access")
        ? { can_write: canWrite }
        : path.endsWith("/{memory_id}")
          ? record(0)
          : { items, pagination },
    ),
  );
  http.POST.mockResolvedValue(response(record(42)));
  http.PUT.mockResolvedValue(response(record(0)));
  http.DELETE.mockResolvedValue(response(undefined));
});

test("loads a bounded 1000-record window, pages locally by 20 and never invents a cursor", async () => {
  const user = userEvent.setup();
  setup();
  expect(await screen.findByText("41 records loaded")).toBeTruthy();
  expect(screen.getByText("Bounded list · not a total count")).toBeTruthy();
  expect(screen.getAllByRole("row")).toHaveLength(21);
  expect(screen.queryByText("Memory text 20")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByText("Memory text 20")).toBeTruthy();
  const calls = http.GET.mock.calls.filter(([path]) =>
    path.endsWith("/memories"),
  );
  expect(calls).toHaveLength(1);
  expect(calls[0][1].params.query).toEqual({
    scope: "user",
    subject_id: undefined,
    limit: 1000,
    cursor: undefined,
  });
  expect(screen.getByRole("button", { name: "Previous" })).toHaveProperty(
    "disabled",
    false,
  );
});

test("empty bounded results do not claim completeness; native continuation is explicit", async () => {
  items = [];
  const view = setup();
  expect(
    await screen.findByText(
      "The backend returned a bounded result. This does not prove the collection is empty.",
    ),
  ).toBeTruthy();
  view.unmount();
  pagination = { next_cursor: "opaque-native-cursor" };
  items = [record(1)];
  setup();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(
      http.GET.mock.calls.some(
        ([, options]) => options.params.query.cursor === "opaque-native-cursor",
      ),
    ).toBe(true),
  );
});

test("semantic search has independent results and options, including threshold zero", async () => {
  http.POST.mockResolvedValue(
    response({ items: [{ ...record(999), score: 0 }], pagination: null }),
  );
  const user = userEvent.setup();
  setup();
  await screen.findByText("41 records loaded");
  await user.type(
    screen.getByRole("searchbox", { name: "Search memories" }),
    "remember language",
  );
  await user.click(screen.getByRole("button", { name: "Search options" }));
  await user.type(
    screen.getByRole("spinbutton", { name: "Similarity threshold" }),
    "0",
  );
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(await screen.findByText("Memory text 999")).toBeTruthy();
  expect(screen.getByText("0")).toBeTruthy();
  expect(http.POST.mock.calls[0][1].body).toEqual({
    query: "remember language",
    limit: 20,
    threshold: 0,
  });
  expect(screen.queryByText("Memory text 0")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Back to list" }));
  expect(screen.getByText("Memory text 0")).toBeTruthy();
});

test("turning Semantic off filters only the loaded window and never queries the backend", async () => {
  const user = userEvent.setup();
  setup();
  await screen.findByText("41 records loaded");
  await user.click(screen.getByRole("button", { name: "Semantic" }));
  await user.type(
    screen.getByRole("searchbox", { name: "Search memories" }),
    "Memory text 7",
  );
  expect(await screen.findByText("1 of 41 loaded records match")).toBeTruthy();
  expect(screen.getByText("Memory text 7")).toBeTruthy();
  expect(screen.queryByText("Memory text 0")).toBeNull();
  expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
  expect(http.POST).not.toHaveBeenCalled();
});

test("subject permission projection controls read-only UI independently of provider administration", async () => {
  canWrite = false;
  setup();
  const user = userEvent.setup();
  await screen.findByText("Read only");
  expect(screen.queryByRole("button", { name: "Add memory" })).toBeNull();
  await user.click((await screen.findAllByRole("row"))[1]);
  const panel = await screen.findByRole("complementary", { name: "Memory" });
  expect(await within(panel).findByText("Memory text 0")).toBeTruthy();
  expect(within(panel).queryByRole("textbox")).toBeNull();
  expect(within(panel).queryByRole("button", { name: "Save" })).toBeNull();
});

test("uncertain add preserves exact text, does not retry, and requires inspection and acknowledgement", async () => {
  http.POST.mockRejectedValueOnce(new TypeError("Network response lost"));
  const user = userEvent.setup();
  setup();
  await user.click(await screen.findByRole("button", { name: "Add memory" }));
  const text = "  Keep this exactly.  ";
  await user.type(screen.getByRole("textbox", { name: "Memory text" }), text);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Change not confirmed");
  expect(http.POST).toHaveBeenCalledTimes(1);
  expect(http.POST.mock.calls[0][1].body).toEqual({ text });
  expect(screen.getByRole("textbox", { name: "Memory text" })).toHaveProperty(
    "value",
    text,
  );
  expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
    "disabled",
    true,
  );
  http.POST.mockResolvedValueOnce(
    response({ items: [{ id: "committed", memory: text }], pagination: null }),
  );
  await user.click(
    screen.getByRole("button", { name: "Inspect current state" }),
  );
  await screen.findByText(/Current semantic matches are shown below/);
  expect(http.POST.mock.calls[1][0]).toMatch(/\/search$/);
  await user.click(
    screen.getByRole("button", { name: "I checked; allow another attempt" }),
  );
  expect(screen.getByRole("button", { name: "Save" })).toHaveProperty(
    "disabled",
    false,
  );
  expect(http.POST).toHaveBeenCalledTimes(2);
});

test("uncertain delete is never automatically repeated, and inspection can observe absence", async () => {
  http.DELETE.mockRejectedValueOnce(new TypeError("Response lost"));
  const user = userEvent.setup();
  setup(
    <MemoryRecordEditor
      target={target}
      recordId="native-0"
      canWrite
      onClose={vi.fn()}
    />,
  );
  await deleteMemory(user);
  await screen.findByText("Change not confirmed");
  http.GET.mockRejectedValueOnce(
    new ApiError(404, "memory_not_found", "Memory not found.", {}, null),
  );
  await user.click(
    screen.getByRole("button", { name: "Inspect current state" }),
  );
  await screen.findByText("This memory is no longer present.");
  expect(http.DELETE).toHaveBeenCalledTimes(1);
});

test("all content requests bind provider and immutable subject; user requests omit any supplied subject", async () => {
  const scoped = {
    ...target,
    provider_id: "memprov_old",
    scope: "thread" as const,
    subject_id: "thr_stable",
  };
  const api = memoryApi({ http } as unknown as Client, scoped);
  await api.list(new AbortController().signal);
  await api.update("opaque/native id", "edit");
  expect(http.GET.mock.calls[0][1].params).toMatchObject({
    path: { workspace: target.workspace, provider_id: "memprov_old" },
    query: { scope: "thread", subject_id: "thr_stable" },
  });
  expect(http.PUT.mock.calls[0][1].params.path.memory_id).toBe(
    "opaque/native id",
  );
  await memoryApi({ http } as unknown as Client, {
    ...target,
    subject_id: "forged",
  }).list(new AbortController().signal);
  expect(
    http.GET.mock.calls.at(-1)?.[1].params.query.subject_id,
  ).toBeUndefined();
  expect(memoryKey(scoped)).not.toEqual(
    memoryKey({ ...scoped, provider_id: "memprov_new" }),
  );
  expect(memoryKey(scoped)).not.toEqual(
    memoryKey({ ...scoped, subject_id: "thr_other" }),
  );
});

test("confirmed update and 204 deletion close successfully without another write", async () => {
  const user = userEvent.setup(),
    onClose = vi.fn();
  const view = setup(
    <MemoryRecordEditor
      target={target}
      recordId="native-0"
      canWrite
      onClose={onClose}
    />,
  );
  const input = await screen.findByRole("textbox", { name: "Memory text" });
  await user.clear(input);
  await user.type(input, "  Updated verbatim  ");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(http.PUT.mock.calls[0][1].body).toEqual({
    text: "  Updated verbatim  ",
  });
  view.unmount();
  onClose.mockClear();
  setup(
    <MemoryRecordEditor
      target={target}
      recordId="native-0"
      canWrite
      onClose={onClose}
    />,
  );
  await deleteMemory(user);
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(http.DELETE).toHaveBeenCalledOnce();
  expect(screen.queryByText("Change not confirmed")).toBeNull();
});

test.each([false, true])(
  "access refresh failure preserves an open draft and uncertain state (%s)",
  async (uncertain) => {
    const user = userEvent.setup(),
      onEditing = vi.fn();
    const { cache } = setup(
      <MemoryContents target={target} onEditing={onEditing} />,
    );
    await user.click(await screen.findByRole("button", { name: "Add memory" }));
    await user.type(
      screen.getByRole("textbox", { name: "Memory text" }),
      "Keep my draft",
    );
    if (uncertain) {
      http.POST.mockRejectedValueOnce(new TypeError("Lost response"));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText("Change not confirmed");
    }
    http.GET.mockRejectedValueOnce(new Error("Permission refresh unavailable"));
    await cache.invalidateQueries({
      queryKey: [...memoryKey(target), "access"],
    });
    const dialog = await screen.findByRole("complementary", {
      name: "Add memory",
    });
    await within(dialog).findByText("Permission refresh unavailable");
    expect(within(dialog).getByText("Keep my draft")).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "Save" })).toBeNull();
    expect(onEditing).not.toHaveBeenCalledWith(false);
    await user.click(within(dialog).getByRole("button", { name: "Reload" }));
    expect(
      await within(dialog).findByRole("textbox", { name: "Memory text" }),
    ).toHaveProperty("value", "Keep my draft");
    expect(within(dialog).getByRole("button", { name: "Save" })).toHaveProperty(
      "disabled",
      uncertain,
    );
    if (uncertain)
      expect(within(dialog).getByText("Change not confirmed")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onEditing).toHaveBeenLastCalledWith(false));
  },
);
