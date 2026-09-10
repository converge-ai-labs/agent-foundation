import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { AgentAvatar } from "./avatar";

afterEach(cleanup);

it("keeps an existing agent's color when renamed or remounted", () => {
  const { rerender, unmount } = render(
    <AgentAvatar id="agt_stable" name="Alpha" />,
  );
  const color = screen.getByText("A").style.backgroundColor;
  rerender(<AgentAvatar id="agt_stable" name="Beta" />);
  expect(screen.getByText("B").style.backgroundColor).toBe(color);
  unmount();
  render(<AgentAvatar id="agt_stable" name="Beta" />);
  expect(screen.getByText("B").style.backgroundColor).toBe(color);
});

it("previews name changes and handles non-Latin and empty names", () => {
  const { rerender } = render(<AgentAvatar name="  alpha" />);
  expect(screen.getByText("A")).toBeDefined();
  rerender(<AgentAvatar name="研究助手" />);
  expect(screen.getByText("研")).toBeDefined();
  rerender(<AgentAvatar name="   " />);
  expect(screen.getByText("A")).toBeDefined();
});
