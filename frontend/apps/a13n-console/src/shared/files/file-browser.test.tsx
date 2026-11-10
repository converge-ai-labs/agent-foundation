import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileExplorer } from "./file-explorer";
import { FileText } from "./file-browser";
import { fileTree } from "./tree";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
// CodeMirror geometry and native selection are checked in the real browser.
vi.mock("./text-editor", () => ({ default: () => null }));
afterEach(cleanup);

it("navigates the tree with one tab stop, collapses folders and activates files", async () => {
  const user = userEvent.setup(),
    select = vi.fn();
  render(
    <FileExplorer
      label="Files"
      nodes={fileTree([
        { path: "notes/a.md" },
        { path: "notes/b.md" },
        { path: "README.md" },
      ])}
      selected="README.md"
      select={select}
    />,
  );
  await user.tab();
  expect(document.activeElement).toBe(
    screen.getByRole("treeitem", { name: "README.md" }),
  );
  await user.keyboard("{Home}{ArrowLeft}");
  expect(
    screen
      .getByRole("treeitem", { name: "notes" })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.queryByRole("treeitem", { name: "a.md" })).toBeNull();
  await user.keyboard("{ArrowRight}{ArrowRight}{ArrowDown}{Enter}");
  expect(select).toHaveBeenCalledWith("notes/b.md");
  await user.keyboard("{ArrowLeft}");
  expect(document.activeElement).toBe(
    screen.getByRole("treeitem", { name: "notes" }),
  );
});
it("copies the exact raw file including CRLF, blank lines and trailing whitespace", async () => {
  const user = userEvent.setup();
  const text = "\talpha  \r\n\r\nlast line";
  render(<FileText text={text} preview={false} />);
  await user.click(screen.getByRole("button", { name: "Copy file" }));
  expect(await navigator.clipboard.readText()).toBe(text);
  expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy();
});
