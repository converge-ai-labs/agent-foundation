import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MarkdownContent } from "../../shared/markdown";

it.each([false, true])(
  "renders untrusted model Markdown safely with compact links %s",
  (compactLinks) => {
    const markup = renderToStaticMarkup(
      <MarkdownContent
        compactLinks={compactLinks}
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
  },
);

it.each([false, true])(
  "ends www autolinks at CJK punctuation in Markdown (agent reply: %s)",
  (agentReply) => {
    const label = "www.example.com/wiki/人工智能";
    render(<MarkdownContent agentReply text={`另见（${label}）了解更多。`} />);
    const link = screen.getByRole("link", { name: label });
    expect(link.getAttribute("href")).toBe(encodeURI(`http://${label}`));
    expect(link.nextSibling?.textContent).toBe("）了解更多。");
  },
);

function Destination() {
  const location = useLocation();
  return <output>{location.pathname + location.search + location.hash}</output>;
}

it("navigates a resource reference in Console, retaining its revision and fragment", async () => {
  const destination =
    "/workspace/ws_example/agents/agt_example?revision=rev_example#configuration";
  render(
    <MemoryRouter initialEntries={["/finding"]}>
      <Routes>
        <Route
          path="/finding"
          element={
            <MarkdownContent
              text={`Target: [Agent: Release assistant](${window.location.origin}${destination} "Exact revision")`}
            />
          }
        />
        <Route
          path="/workspace/:workspaceId/agents/:agentId"
          element={<Destination />}
        />
      </Routes>
    </MemoryRouter>,
  );
  const link = screen.getByRole("link", { name: "Agent: Release assistant" });
  expect(link.getAttribute("href")).toBe(destination);
  expect(link.getAttribute("target")).toBeNull();
  expect(link.getAttribute("title")).toBe("Exact revision");
  await userEvent.click(link);
  expect(screen.getByRole("status").textContent).toBe(destination);
});

it("keeps Run task labels and formatted labels, without trusting resource-like external labels", () => {
  const run =
    "/workspace/ws_example/sessions/ses_example/threads/thr_example/runs/run_example?view=chat";
  render(
    <MemoryRouter>
      <MarkdownContent
        text={[
          `[Run: First failed attempt](${run})`,
          `[**Release assistant**](/workspace/ws_example/agents/agt_example)`,
          `[Agent: External guide](https://example.com/workspace/ws_example/agents/agt_example)`,
        ].join("\n\n")}
      />
    </MemoryRouter>,
  );
  expect(
    screen
      .getByRole("link", { name: "Run: First failed attempt" })
      .getAttribute("href"),
  ).toBe(run);
  expect(
    screen
      .getByRole("link", { name: "Agent: Release assistant" })
      .querySelector("strong")?.textContent,
  ).toBe("Release assistant");
  const external = screen.getByRole("link", { name: "Agent: External guide" });
  expect(external.getAttribute("target")).toBe("_blank");
  expect(external.getAttribute("rel")).toBe("noopener noreferrer");
  expect(external.querySelector("svg")).toBeNull();
});
