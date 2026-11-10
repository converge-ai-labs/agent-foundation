// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { FileText } from "./file-browser";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it.each([
  "alpha\n\nbeta\n",
  "alpha\n\nbeta",
  "\n",
  "alpha\n\n",
  "alpha\r\n\r\nbeta\r\n",
  "\tindented  \n    \nlast line",
])("preserves source characters and newline separators for %j", (text) => {
  const { container } = render(<FileText text={text} preview={false} />);
  expect(
    Array.from(
      container.querySelectorAll("code"),
      (line) => line.textContent,
    ).join(""),
  ).toBe(text);
});
