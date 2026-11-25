// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import {
  HtmlCodePreview,
  HtmlPreviewEnabled,
  isHtmlPreviewTooLarge,
} from "../src/html-code-preview";

afterEach(cleanup);

function Block({
  source = "<p>Test</p>",
  enabled = true,
}: {
  source?: string;
  enabled?: boolean;
}) {
  return (
    <HtmlPreviewEnabled value={enabled}>
      <HtmlCodePreview
        source={source}
        labels={{
          preview: "Preview",
          title: "HTML preview",
          tooLarge: "Too large",
        }}
      >
        {({ actions, preview, hideSource }) => (
          <>
            {actions}
            {preview}
            <pre hidden={hideSource}>{source}</pre>
          </>
        )}
      </HtmlCodePreview>
    </HtmlPreviewEnabled>
  );
}

it("uses a UTF-8 byte limit, not a character limit", () => {
  expect(isHtmlPreviewTooLarge("a".repeat(200_000))).toBe(false);
  expect(isHtmlPreviewTooLarge("a".repeat(200_001))).toBe(true);
  expect(isHtmlPreviewTooLarge("界".repeat(66_667))).toBe(true);
});

it("starts in source, mounts only on demand and removes the iframe on toggle", async () => {
  const user = userEvent.setup();
  const view = render(<Block enabled={false} />);
  expect(screen.queryByRole("button", { name: "Preview" })).toBeNull();
  view.rerender(<Block />);
  const button = screen.getByRole("button", { name: "Preview" });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(screen.queryByTitle("HTML preview")).toBeNull();
  await user.click(button);
  const frame = await screen.findByTitle<HTMLIFrameElement>("HTML preview");
  expect(frame.getAttribute("sandbox")).toBe("");
  expect(frame.getAttribute("srcdoc")).toContain("<p>Test</p>");
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(view.container.querySelector("pre")?.hidden).toBe(true);
  await user.click(button);
  expect(screen.queryByTitle("HTML preview")).toBeNull();
  expect(view.container.querySelector("pre")?.hidden).toBe(false);
});

it("keeps oversized HTML as source and explains why there is no preview", async () => {
  const user = userEvent.setup();
  const view = render(<Block source={"a".repeat(200_001)} />);
  await user.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.queryByTitle("HTML preview")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("Too large");
  expect(view.container.querySelector("pre")?.hidden).toBe(false);
});
