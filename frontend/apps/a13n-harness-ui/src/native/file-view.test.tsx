// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
vi.mock("./file-media", () => ({
  FileMedia: () => <div data-testid="media-preview" />,
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
