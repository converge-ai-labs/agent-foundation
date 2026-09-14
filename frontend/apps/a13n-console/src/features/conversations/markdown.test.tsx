import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MarkdownContent } from "../../shared/markdown";

it("renders untrusted model Markdown without executable HTML or automatic remote image loads", () => {
  const markup = renderToStaticMarkup(
    <MarkdownContent
      text={
        "<script>alert(1)</script>\n\n[bad](javascript:alert%281%29)\n\n![report](https://example.com/tracker.png)\n\n**Result**"
      }
    />,
  );
  expect(markup).not.toContain("<script");
  expect(markup).not.toContain("javascript:");
  expect(markup).not.toContain("<img");
  expect(markup).toContain("<strong>Result</strong>");
  expect(markup).toContain('rel="noopener noreferrer"');
});
