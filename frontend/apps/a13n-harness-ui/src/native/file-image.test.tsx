// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { FileBuffers, type FileBuffer } from "./buffer";
import { downloadBlob } from "./capture";
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
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function metadata(
  path = "/code/screen.png",
  size = 88_047,
): Schema<"FileText"> {
  return {
    entry: {
      path,
      size,
      revision: "first",
      kind: "file",
      modified_ns: 0,
      mode: 0,
    },
    resolved_path: path,
    presentation: size > 512 * 1024 ? "too_large" : "binary",
    text: null,
  };
}
function fixture(initial = metadata()) {
  let current = initial;
  const get = vi.fn(async () => ({ data: current }));
  const post = vi.fn();
  const blob = new Blob(["image bytes"], { type: "application/octet-stream" });
  const fetch = vi.fn(async (_url: string, _init?: RequestInit) => ({
    blob: async () => blob,
  }));
  const create = vi.fn().mockReturnValue("blob:preview");
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const buffers = new Map<string, FileBuffer>();
  const transport = {
    client: { GET: get, POST: post },
    fetch,
  } as unknown as Transport;
  const tree = () => (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <FileBuffers value={buffers}>
          <FileView
            path={current.entry.path}
            open={vi.fn()}
            refresh={vi.fn()}
          />
        </FileBuffers>
      </TransportContext>
    </QueryClientProvider>
  );
  return {
    tree,
    get,
    post,
    fetch,
    blob,
    create,
    revoke,
    update(next: Schema<"FileText">) {
      current = next;
    },
  };
}
async function loaded(name = "screen.png") {
  const image = await screen.findByAltText(name);
  Object.defineProperties(image, {
    naturalWidth: { value: 1920 },
    naturalHeight: { value: 1080 },
  });
  fireEvent.load(image);
  await screen.findByText("1920 × 1080");
  return image;
}

it.each([
  ["screen.png", 88_047],
  ["photo.JPG", 600_000],
  ["photo.jpeg", 800],
  ["animated.gif", 900],
  ["screen.webp", 10 * 1024 * 1024],
])(
  "previews %s at %i bytes without treating it as editable text",
  async (name, size) => {
    const f = fixture(metadata(`/code/${name}`, size));
    const view = render(f.tree());
    const image = await loaded(name);
    expect(image.getAttribute("src")).toBe("blob:preview");
    const [url, options] = f.fetch.mock.calls[0]!;
    const query = new URL(url, "http://localhost").searchParams;
    expect(query.get("path")).toBe(`/code/${name}`);
    expect(query.get("expected_revision")).toBe("first");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(f.post).not.toHaveBeenCalled();
    view.unmount();
    expect(options?.signal?.aborted).toBe(true);
    expect(f.revoke).toHaveBeenCalledWith("blob:preview");
  },
);

it("expands the loaded bytes in the shared viewer and keeps ordinary download available", async () => {
  const f = fixture();
  render(f.tree());
  await loaded();
  fireEvent.click(
    screen.getByRole("button", { name: "Expand image: screen.png" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "screen.png" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Actual size" }));
  expect(
    within(dialog).getByRole("button", { name: "Fit to screen" }),
  ).toBeTruthy();
  expect(
    within(dialog)
      .getByRole("link", { name: "Download image" })
      .getAttribute("href"),
  ).toBe("blob:preview");
  expect(f.fetch).toHaveBeenCalledTimes(1);
  expect(f.post).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Close image preview" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() =>
    expect(downloadBlob).toHaveBeenCalledWith(f.blob, "screen.png"),
  );
});

it("does not fetch oversize images or unsupported binary formats", async () => {
  const f = fixture(metadata("/code/huge.png", 10 * 1024 * 1024 + 1));
  const view = render(f.tree());
  await screen.findByText("Image exceeds the preview limit");
  expect(
    (screen.getByRole("button", { name: "Download" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(f.fetch).not.toHaveBeenCalled();
  f.update(metadata("/code/document.pdf"));
  view.rerender(f.tree());
  await screen.findByText("Binary file");
  expect(f.fetch).not.toHaveBeenCalled();
  const svg = metadata("/code/icon.svg");
  f.update({ ...svg, presentation: "text", text: "<svg />" });
  view.rerender(f.tree());
  await screen.findByRole("textbox");
  expect(screen.queryByRole("region", { name: "Image preview" })).toBeNull();
  expect(f.fetch).not.toHaveBeenCalled();
});

it("previews an extensionless symlink using its resolved image name and requested path", async () => {
  const f = fixture({
    ...metadata("/code/latest"),
    resolved_path: "/code/actual.png",
  });
  render(f.tree());
  await loaded("latest");
  expect(
    new URL(f.fetch.mock.calls[0]![0], "http://localhost").searchParams.get(
      "path",
    ),
  ).toBe("/code/latest");
});

it("refreshes a changed revision and retries a failed decode even when the revision is unchanged", async () => {
  const f = fixture();
  f.create
    .mockReturnValueOnce("blob:first")
    .mockReturnValueOnce("blob:second")
    .mockReturnValue("blob:retry");
  render(f.tree());
  await loaded();
  f.update({
    ...metadata(),
    entry: { ...metadata().entry, revision: "second" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(screen.getByAltText("screen.png").getAttribute("src")).toBe(
      "blob:second",
    ),
  );
  expect(f.revoke).toHaveBeenCalledWith("blob:first");
  expect(
    new URL(f.fetch.mock.calls[1]![0], "http://localhost").searchParams.get(
      "expected_revision",
    ),
  ).toBe("second");
  fireEvent.error(screen.getByAltText("screen.png"));
  await screen.findByText(/This image cannot be previewed/);
  expect(screen.getByRole("button", { name: "Download" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.getByAltText("screen.png").getAttribute("src")).toBe(
      "blob:retry",
    ),
  );
  expect(f.revoke).toHaveBeenCalledWith("blob:second");
  expect(f.fetch).toHaveBeenCalledTimes(3);
});

it("reports a content revision conflict and re-observes disk before retrying", async () => {
  const f = fixture();
  f.fetch.mockRejectedValueOnce(
    new ApiError(
      "File content changed; refresh before selecting or downloading.",
      409,
    ),
  );
  render(f.tree());
  await screen.findByText(/File content changed/);
  expect(f.create).not.toHaveBeenCalled();
  f.update({ ...metadata(), entry: { ...metadata().entry, revision: "new" } });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await loaded();
  expect(
    new URL(f.fetch.mock.calls.at(-1)![0], "http://localhost").searchParams.get(
      "expected_revision",
    ),
  ).toBe("new");
});

it("aborts a replaced view and ignores late response bodies", async () => {
  const f = fixture();
  let resolveBody!: (blob: Blob) => void;
  f.fetch.mockResolvedValueOnce({
    blob: () =>
      new Promise((resolve) => {
        resolveBody = resolve;
      }),
  });
  const view = render(f.tree());
  await waitFor(() => expect(resolveBody).toBeDefined());
  f.update(metadata("/code/next.png"));
  view.rerender(f.tree());
  await loaded("next.png");
  expect(f.fetch.mock.calls[0]![1]?.signal?.aborted).toBe(true);
  resolveBody(f.blob);
  await waitFor(() => expect(screen.queryByAltText("screen.png")).toBeNull());
  expect(f.create).toHaveBeenCalledTimes(1);
});
