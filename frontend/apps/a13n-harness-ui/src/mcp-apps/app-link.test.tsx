// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { externalAppLink, useAppLink } from "./app-link";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it.each([
  "javascript:alert(1)",
  "data:text/html,hello",
  "file:///etc/passwd",
  "https://user:pass@example.com",
  "https://host.test/api/host/files",
  "https://host.test/auth#login",
  "https://sandbox.test/sandbox.html",
  "/api/host/files",
])("rejects nonexternal or privileged destination %s", (uri) => {
  expect(() =>
    externalAppLink(
      uri,
      "https://host.test",
      "https://sandbox.test/sandbox.html",
    ),
  ).toThrow();
});
it("opens an exact external URL only from the Host confirmation click", () => {
  const opened = vi.spyOn(window, "open").mockReturnValue(null);
  function Card() {
    const link = useAppLink();
    return (
      <>
        <button
          onClick={() =>
            void link
              .propose(
                "https://example.com/docs?q=apps",
                "https://sandbox.test/sandbox.html",
              )
              .catch(() => {})
          }
        >
          App request
        </button>
        {link.confirmation}
      </>
    );
  }
  render(<Card />);
  fireEvent.click(screen.getByText("App request"));
  expect(opened).not.toHaveBeenCalled();
  expect(screen.getByText("https://example.com/docs?q=apps")).toBeTruthy();
  fireEvent.click(screen.getByText("Decline link"));
  expect(opened).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("App request"));
  fireEvent.click(screen.getByText("Open external link"));
  expect(opened).toHaveBeenCalledExactlyOnceWith(
    "https://example.com/docs?q=apps",
    "_blank",
    "noopener,noreferrer",
  );
});
