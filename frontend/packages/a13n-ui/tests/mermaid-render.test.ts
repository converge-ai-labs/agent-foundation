// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { renderDiagram } from "../src/mermaid-render";
import type { FrameReply } from "../src/mermaid-protocol";

function reply(frame: HTMLIFrameElement, data: FrameReply, origin = "null") {
  window.dispatchEvent(
    new MessageEvent("message", { source: frame.contentWindow, origin, data }),
  );
}

it("keeps oversized diagrams and unsupported configuration out of the sandbox", async () => {
  await expect(renderDiagram("x".repeat(50_001), false)).resolves.toEqual({
    reason: "Diagram source exceeds 50,000 characters.",
  });
  for (const directive of ["---\nconfig: {}\n---", "%%{init: {}}%%"]) {
    await expect(
      renderDiagram(`${directive}\nflowchart LR\nA --> B`, false),
    ).resolves.toEqual({
      reason: "Diagram configuration directives are not supported in previews.",
    });
  }
  expect(document.querySelector("iframe")).toBeNull();
});

it("queues complete diagrams in one opaque frame, ignores unrelated replies and recovers after timeout", async () => {
  vi.useFakeTimers();
  try {
    const source =
      'flowchart LR\nA@{ shape: rect, label: "First<br/>Second" } --> B\nclassDef note fill:red';
    const first = renderDiagram(source, true);
    const second = renderDiagram("classDiagram\nclass Topic", false);
    await vi.advanceTimersByTimeAsync(0);
    const frame = document.querySelector("iframe")!;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    const post = vi
      .spyOn(frame.contentWindow!, "postMessage")
      .mockImplementation(() => {});
    reply(frame, { kind: "mermaid-ready" }, window.location.origin);
    expect(post).not.toHaveBeenCalled();
    reply(frame, { kind: "mermaid-ready" });
    expect(post).toHaveBeenCalledExactlyOnceWith(
      { kind: "render-mermaid", id: 1, source, dark: true },
      "*",
    );
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 12"/>';
    reply(frame, { kind: "mermaid-result", id: 12, svg });
    reply(frame, { kind: "mermaid-result", id: 1, svg });
    const result = await first;
    expect(result.src).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
    expect(decodeURIComponent(result.src!)).toContain('width="16" height="12"');
    await vi.advanceTimersByTimeAsync(0);
    expect(post).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 2, dark: false }),
      "*",
    );
    await vi.advanceTimersByTimeAsync(8_000);
    await expect(second).resolves.toEqual({
      reason: "Diagram rendering timed out.",
    });
    expect(frame.isConnected).toBe(false);

    const missing = renderDiagram("flowchart LR\nA --> B", false);
    const alsoMissing = renderDiagram("pie title Work", false);
    await vi.advanceTimersByTimeAsync(8_000);
    expect(await Promise.all([missing, alsoMissing])).toEqual([
      { reason: "The Mermaid preview renderer could not start." },
      { reason: "The Mermaid preview renderer could not start." },
    ]);

    const third = renderDiagram("not a diagram", false);
    await vi.advanceTimersByTimeAsync(0);
    const replacement = document.querySelector("iframe")!;
    expect(replacement).not.toBe(frame);
    reply(replacement, { kind: "mermaid-ready" });
    reply(replacement, {
      kind: "mermaid-result",
      id: 5,
      reason: "unsupported-type",
    });
    await expect(third).resolves.toEqual({
      reason: "This Mermaid diagram type is not recognized.",
    });
  } finally {
    vi.useRealTimers();
  }
});
