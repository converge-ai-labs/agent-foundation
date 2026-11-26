// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InputContent } from "./input-content";
import {
  AttachmentPreviewSheet,
  type AttachmentPreviewTarget,
} from "./attachment-preview";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

const copyTextMock = vi.fn();
const findMock = vi.fn();

vi.mock("../platform", async (actual) => ({
  ...(await actual<typeof import("../platform")>()),
  copyText: (...args: unknown[]) => copyTextMock(...args),
}));

vi.mock("../configuration/editor", () => ({
  SourceEditor: ({
    value,
    label,
    wrap,
    ref,
  }: {
    value: string;
    label: string;
    wrap?: boolean;
    ref?: React.Ref<{
      focus: () => void;
      find: () => void;
      goToLine: () => void;
    }>;
  }) => {
    if (ref && typeof ref === "object" && "current" in ref) {
      (ref as { current: unknown }).current = {
        focus: vi.fn(),
        find: findMock,
        goToLine: vi.fn(),
      };
    }
    return (
      <textarea
        aria-label={label}
        data-wrap={String(!!wrap)}
        value={value}
        readOnly
      />
    );
  },
}));

const documentPreviewSpy = vi.fn();
vi.mock("../native/document-preview", () => ({
  DocumentPreview: ({
    kind,
    loadBytes,
  }: {
    kind: string;
    loadBytes?: (signal: AbortSignal) => Promise<ArrayBuffer>;
  }) => {
    documentPreviewSpy({ kind, hasLoader: typeof loadBytes === "function" });
    return (
      <div data-testid="document-preview" data-kind={kind}>
        <button
          type="button"
          onClick={() => void loadBytes?.(new AbortController().signal)}
        >
          Load document bytes
        </button>
      </div>
    );
  },
}));

function stubUrls() {
  let count = 0;
  const createObjectURL = vi.fn(() => `blob:attachment-${++count}`);
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  return { createObjectURL, revokeObjectURL };
}

function makeBlobResponse(
  data: string | Uint8Array,
  type = "text/plain",
): Response {
  const bytes =
    typeof data === "string" ? new TextEncoder().encode(data) : data;
  return {
    blob: async () =>
      ({
        size: bytes.byteLength,
        type,
        arrayBuffer: async () =>
          bytes.buffer.slice(
            bytes.byteOffset,
            bytes.byteOffset + bytes.byteLength,
          ),
      }) as Blob,
  } as Response;
}

beforeEach(() => {
  copyTextMock.mockReset();
  findMock.mockReset();
  documentPreviewSpy.mockReset();
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens a small text attachment in the right-side drawer, supports copy, wrap, find, disclosures, and one-click download without re-fetching", async () => {
  const user = userEvent.setup();
  const urls = stubUrls();
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const text = "Line one\nLine two with details";
  const fetch = vi.fn().mockResolvedValue(makeBlobResponse(text, "text/plain"));
  const attachment: Schema<"ThreadAttachment"> = {
    attachment_id: "att-txt",
    name: "input-text-2.txt",
    media_type: "text/plain",
    size: text.length,
    source: {
      location: "host",
      path: "/repo/input-text-2.txt",
      resolved_path: "/repo/input-text-2.txt",
      revision: "rev-1",
    },
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <InputContent
        threadId="thread-1"
        renderText={(value) => value}
        parts={[
          {
            kind: "user",
            text: "Attached file summary seen by model",
            metadata: { harness_ui: { attachment } },
          },
        ]}
      />
    </TransportContext>,
  );

  expect(fetch).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "input-text-2.txt" }));

  const dialog = await screen.findByRole("dialog", {
    name: "input-text-2.txt",
  });
  const editor = await within(dialog).findByRole("textbox", {
    name: "File text: input-text-2.txt",
  });
  expect((editor as HTMLTextAreaElement).value).toBe(text);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(
    "/api/threads/thread-1/attachments/att-txt",
  );

  // Copy text content
  copyTextMock.mockResolvedValue(undefined);
  await user.click(within(dialog).getByRole("button", { name: "Copy" }));
  expect(copyTextMock).toHaveBeenCalledWith(text);
  await within(dialog).findByRole("button", { name: "Copied" });

  // Wrap toggle and Find button
  const wrapButton = within(dialog).getByRole("button", {
    name: "Wrap long lines",
  });
  expect(wrapButton.getAttribute("aria-pressed")).toBe("true");
  await user.click(wrapButton);
  expect(wrapButton.getAttribute("aria-pressed")).toBe("false");
  await user.click(within(dialog).getByRole("button", { name: "Find" }));
  expect(findMock).toHaveBeenCalledTimes(1);

  // Retained metadata & model-visible disclosures
  expect(within(dialog).getByText("Source and revision")).toBeTruthy();
  expect(within(dialog).getByText("What the model sees")).toBeTruthy();
  expect(
    within(dialog).getByText("Attached file summary seen by model"),
  ).toBeTruthy();
  expect(
    within(dialog).getByText("Server snapshot - /repo/input-text-2.txt"),
  ).toBeTruthy();

  // One-click download reuses the already-fetched Blob (no second network request)
  await user.click(within(dialog).getByRole("button", { name: "Download" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
  expect(clickSpy).toHaveBeenCalledTimes(1);
  await waitFor(() =>
    expect(urls.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1"),
  );
});

it("defaults Markdown attachments to rendered preview and toggles to raw source", async () => {
  const user = userEvent.setup();
  const markdown = "# Release Notes\n\n- **Bold item**";
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse(markdown, "text/markdown"));
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-md",
    attachmentId: "att-md",
    name: "CHANGELOG.md",
    mediaType: "text/markdown",
    size: markdown.length,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={vi.fn()} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "CHANGELOG.md" });
  const article = await within(dialog).findByRole("article", {
    name: "Markdown preview",
  });
  expect(within(article).getByText("Bold item").tagName).toBe("STRONG");
  expect(
    within(dialog)
      .getByRole("button", { name: "Preview" })
      .getAttribute("aria-pressed"),
  ).toBe("true");

  await user.click(within(dialog).getByRole("button", { name: "Text" }));
  const editor = await within(dialog).findByRole("textbox", {
    name: "File text: CHANGELOG.md",
  });
  expect((editor as HTMLTextAreaElement).value).toBe(markdown);
});

it("renders SVG attachments as isolated data-URL images with raw source toggle", async () => {
  const user = userEvent.setup();
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="5"/></svg>';
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse(svg, "image/svg+xml"));
  const attachment: Schema<"ThreadAttachment"> = {
    attachment_id: "att-svg",
    name: "diagram.svg",
    media_type: "image/svg+xml",
    size: svg.length,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <InputContent
        threadId="thread-svg"
        renderText={(value) => value}
        parts={[
          {
            kind: "media",
            metadata: { harness_ui: { attachment } },
          },
        ]}
      />
    </TransportContext>,
  );

  await user.click(screen.getByRole("button", { name: "diagram.svg" }));
  const dialog = await screen.findByRole("dialog", { name: "diagram.svg" });
  const img = await within(dialog).findByRole("img", { name: "diagram.svg" });
  expect(img.getAttribute("src")).toMatch(
    /^data:image\/svg\+xml;charset=utf-8,/,
  );
  // Never inject raw SVG/script markup into the workbench DOM.
  expect(dialog.querySelector("script")).toBeNull();

  await user.click(within(dialog).getByRole("button", { name: "Text" }));
  const editor = await within(dialog).findByRole("textbox", {
    name: "File text: diagram.svg",
  });
  expect((editor as HTMLTextAreaElement).value).toBe(svg);
});

it("does not eagerly fetch oversized text attachments and downloads on click", async () => {
  const user = userEvent.setup();
  const urls = stubUrls();
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse("large payload", "text/plain"));
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-big",
    attachmentId: "att-big",
    name: "huge.log",
    mediaType: "text/plain",
    size: 600 * 1024,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={vi.fn()} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "huge.log" });
  expect(
    within(dialog).getByText("File exceeds the text preview limit"),
  ).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();

  const downloadButtons = within(dialog).getAllByRole("button", {
    name: "Download",
  });
  await user.click(downloadButtons[0]);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
  expect(clickSpy).toHaveBeenCalledTimes(1);
});

it("falls back to binary notice for NUL-byte payloads and reuses fetched bytes on download", async () => {
  const user = userEvent.setup();
  const urls = stubUrls();
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const binary = new Uint8Array([0x50, 0x4b, 0x00, 0x04]);
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse(binary, "application/octet-stream"));
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-bin",
    attachmentId: "att-bin",
    name: "payload.dat",
    mediaType: "application/octet-stream",
    size: binary.byteLength,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={vi.fn()} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "payload.dat" });
  await within(dialog).findByText(
    "Binary attachment. Download to inspect its original bytes.",
  );
  expect(fetch).toHaveBeenCalledTimes(1);

  await user.click(
    within(dialog).getAllByRole("button", { name: "Download" })[0],
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
  expect(clickSpy).toHaveBeenCalledTimes(1);
});

it("delegates PDF attachments to DocumentPreview with a shared byte loader", async () => {
  const user = userEvent.setup();
  const urls = stubUrls();
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse("%PDF-1.7", "application/pdf"));
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-pdf",
    attachmentId: "att-pdf",
    name: "report.pdf",
    mediaType: "application/pdf",
    size: 4096,
  };

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={vi.fn()} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "report.pdf" });
  const preview = within(dialog).getByTestId("document-preview");
  expect(preview.getAttribute("data-kind")).toBe("pdf");
  expect(documentPreviewSpy).toHaveBeenCalledWith({
    kind: "pdf",
    hasLoader: true,
  });

  await user.click(
    within(dialog).getByRole("button", { name: "Load document bytes" }),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

  // Download reuses the blob cached during document loading.
  await user.click(within(dialog).getByRole("button", { name: "Download" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(urls.createObjectURL).toHaveBeenCalledTimes(1);
  expect(clickSpy).toHaveBeenCalledTimes(1);
});

it("previews local staged text files directly without network requests", async () => {
  const fetch = vi.fn();
  const file = new File(["local draft text"], "draft-note.txt", {
    type: "text/plain",
  });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("local draft text").buffer,
  });

  render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet
        target={{ kind: "local", file }}
        onClose={vi.fn()}
      />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "draft-note.txt" });
  const editor = await within(dialog).findByRole("textbox", {
    name: "File text: draft-note.txt",
  });
  expect((editor as HTMLTextAreaElement).value).toBe("local draft text");
  expect(fetch).not.toHaveBeenCalled();
});

it("previews audio attachments with native controls and revokes the object URL on close", async () => {
  const urls = stubUrls();
  const fetch = vi
    .fn()
    .mockResolvedValue(makeBlobResponse("mp3-bytes", "audio/mpeg"));
  const target: AttachmentPreviewTarget = {
    kind: "retained",
    threadId: "thread-audio",
    attachmentId: "att-audio",
    name: "memo.mp3",
    mediaType: "audio/mpeg",
    size: 1024,
  };

  const view = render(
    <TransportContext value={{ fetch } as unknown as Transport}>
      <AttachmentPreviewSheet target={target} onClose={vi.fn()} />
    </TransportContext>,
  );

  const dialog = await screen.findByRole("dialog", { name: "memo.mp3" });
  const audio = await waitFor(() => {
    const el = dialog.querySelector("audio");
    expect(el).toBeTruthy();
    return el!;
  });
  expect(audio.getAttribute("src")).toBe("blob:attachment-1");
  expect(audio.controls).toBe(true);

  fireEvent.error(audio);
  await within(dialog).findByText(
    "This audio cannot be previewed. The browser may not support its codec. Download the original to inspect it.",
  );

  view.unmount();
  expect(urls.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1");
});
