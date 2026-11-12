// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useImperativeHandle, useLayoutEffect, type Ref } from "react";
import type {
  EditorCursor,
  SourceEditorHandle,
} from "../configuration/editor-view";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { FileBuffer, FileBuffers } from "./buffer";
import { FileView } from "./file-view";

const find = vi.hoisted(() => vi.fn());
vi.mock("../configuration/editor", () => ({
  SourceEditor: ({
    value,
    label,
    ref,
    onCursor,
  }: {
    value: string;
    label: string;
    ref?: Ref<SourceEditorHandle>;
    onCursor?: (cursor: EditorCursor) => void;
  }) => {
    useImperativeHandle(ref, () => ({
      find,
      focus: vi.fn(),
      goToLine: vi.fn(),
    }));
    useLayoutEffect(
      () => onCursor?.({ line: 1, column: 1, selected: 0 }),
      [onCursor],
    );
    return <textarea aria-label={label} value={value} readOnly />;
  },
}));
vi.mock("./capture", () => ({
  CaptureContext: () => null,
  downloadBlob: vi.fn(),
}));
vi.mock("./file-media", () => ({
  FileMedia: () => <div data-testid="media-preview" />,
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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
it.each(["text", "binary"] as const)(
  "inspects ambiguous .ts bytes before selecting the %s view",
  async (presentation) => {
    const path = "/code/checkout.ts";
    const value = file("export const ready = true;", "one", path);
    const info = {
      entry: value.entry,
      resolved_path: path,
      media_type: "video/mp2t",
    };
    const GET = vi.fn(async (url: string) => ({
      data:
        url === "/api/host/files/info"
          ? info
          : {
              ...value,
              presentation,
              text: presentation === "text" ? value.text : null,
            },
    }));
    const queries = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queries}>
        <TransportContext value={{ client: { GET } } as unknown as Transport}>
          <FileBuffers value={new Map()}>
            <FileView path={path} open={vi.fn()} />
          </FileBuffers>
        </TransportContext>
      </QueryClientProvider>,
    );
    if (presentation === "text") {
      expect(
        (
          (await screen.findByLabelText(
            `File text: ${path}`,
          )) as HTMLTextAreaElement
        ).value,
      ).toBe(value.text);
      expect(screen.queryByTestId("media-preview")).toBeNull();
    } else {
      expect(await screen.findByTestId("media-preview")).toBeTruthy();
      expect(screen.queryByLabelText(`File text: ${path}`)).toBeNull();
    }
    expect(GET).toHaveBeenCalledWith(
      "/api/host/files/text",
      expect.objectContaining({
        params: { query: { path, expected_revision: "one" } },
      }),
    );
  },
);

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
          <FileView path={path} open={vi.fn()} />
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

it.each([false, true])(
  "keeps Find with the opened file without stealing another editor's focus (%s)",
  async (movedFocus) => {
    const path = "/code/file.txt";
    const value = file("Find this text", "one", path);
    const composer = document.createElement("textarea");
    document.body.append(composer);
    if (movedFocus) composer.focus();
    const queries = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const GET = vi.fn(async () => ({ data: value }));
    try {
      render(
        <QueryClientProvider client={queries}>
          <TransportContext value={{ client: { GET } } as unknown as Transport}>
            <FileBuffers value={new Map([[path, new FileBuffer(value)]])}>
              <FileView path={path} open={vi.fn()} />
            </FileBuffers>
          </TransportContext>
        </QueryClientProvider>,
      );
      await screen.findByLabelText(`File text: ${path}`);
      const preview = screen.getByRole("region", { name: "File content" });
      expect(document.activeElement).toBe(movedFocus ? composer : preview);
      preview.focus();
      expect(fireEvent.keyDown(preview, { key: "f", ctrlKey: true })).toBe(
        false,
      );
      expect(find).toHaveBeenCalledTimes(1);
      const refresh = screen.getByRole("button", { name: "Refresh" });
      refresh.focus();
      expect(fireEvent.keyDown(refresh, { key: "f", ctrlKey: true })).toBe(
        false,
      );
      expect(find).toHaveBeenCalledTimes(2);
    } finally {
      composer.remove();
      queries.clear();
    }
  },
);
