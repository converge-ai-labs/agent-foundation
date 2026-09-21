// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { FileBuffer, FileBuffers } from "./buffer";
import { FileView } from "./file-view";

vi.mock("../configuration/editor", () => ({
  SourceEditor: ({ value, label }: { value: string; label: string }) => (
    <textarea aria-label={label} value={value} readOnly />
  ),
}));
vi.mock("./capture", () => ({
  CaptureContext: () => null,
  downloadBlob: vi.fn(),
}));
afterEach(cleanup);

function file(
  text: string,
  revision: string,
  path = "/code/file",
): Schema<"FileText"> {
  return {
    resolved_path: path,
    presentation: "text",
    text,
    entry: {
      path,
      revision,
      kind: "file",
      size: text.length,
      modified_ns: 0,
      mode: 0,
    },
  };
}
function setup() {
  const buffer = new FileBuffer(file("Original disk text", "one"));
  buffer.value = "Private local text";
  buffer.observe(file("Inspected disk text", "two"));
  const GET = vi.fn(async () => ({ data: buffer.observed }));
  const PUT = vi.fn();
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const buffers = new Map([["/code/file", buffer]]);
  const tree = () => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, PUT } } as unknown as Transport}
      >
        <FileBuffers value={buffers}>
          <FileView path="/code/file" refresh={vi.fn()} open={vi.fn()} />
        </FileBuffers>
      </TransportContext>
    </QueryClientProvider>
  );
  return { ...render(tree()), tree, buffer, PUT, user: userEvent.setup() };
}
async function openConfirmation(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  const trigger = screen.getByRole("button", { name });
  await waitFor(() =>
    expect((trigger as HTMLButtonElement).disabled).toBe(false),
  );
  await user.click(trigger);
  return screen.findByRole("dialog");
}

it("opens Markdown in Preview and switches between rendered and Text buttons", async () => {
  const path = "/code/readme.md";
  const value = file("# Rendered heading\n\nBody", "one", path);
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const buffers = new Map([[path, new FileBuffer(value)]]);
  const GET = vi.fn(async () => ({ data: value }));
  render(
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, PUT: vi.fn() } } as unknown as Transport}
      >
        <FileBuffers value={buffers}>
          <FileView path={path} refresh={vi.fn()} open={vi.fn()} />
        </FileBuffers>
      </TransportContext>
    </QueryClientProvider>,
  );
  const preview = screen.getByRole("button", { name: "Preview" });
  const text = screen.getByRole("button", { name: "Text" });
  expect(preview.getAttribute("aria-pressed")).toBe("true");
  expect(text.getAttribute("aria-pressed")).toBe("false");
  expect(
    screen.getByRole("heading", { name: "Rendered heading" }),
  ).toBeTruthy();
  expect(screen.queryByLabelText(`File text: ${path}`)).toBeNull();
  await userEvent.click(text);
  expect(text.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByLabelText(`File text: ${path}`)).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "Rendered heading" }),
  ).toBeNull();
});

it("keeps local text on cancel and only adopts disk text after confirmation", async () => {
  const { buffer, PUT, user } = setup();
  let dialog = await openConfirmation(user, "Use disk version");
  expect(buffer.value).toBe("Private local text");
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(buffer.value).toBe("Private local text");
  expect(buffer.base.entry.revision).toBe("one");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  dialog = await openConfirmation(user, "Use disk version");
  await user.click(
    within(dialog).getByRole("button", { name: "Use disk version" }),
  );
  expect(buffer.value).toBe("Inspected disk text");
  expect(buffer.base.entry.revision).toBe("two");
  expect(buffer.dirty).toBe(false);
  expect(PUT).not.toHaveBeenCalled();
});

it("dismisses stale confirmation if the inspected disk revision changes", async () => {
  const { buffer, user, rerender, tree } = setup();
  await openConfirmation(user, "Use disk version");
  buffer.observe(file("New unseen disk text", "three"));
  rerender(tree());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(buffer.value).toBe("Private local text");
  expect(buffer.base.entry.revision).toBe("one");
});
