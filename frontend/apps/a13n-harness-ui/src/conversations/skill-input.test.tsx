// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { InputContent, inputCopyText } from "./input-content";
import { previewInput } from "./local-input";

afterEach(cleanup);
it("renders identical preview and historical references while copying only authored text", () => {
  const catalog = {
    catalog_id: "a".repeat(64),
    context_kind: "draft" as const,
    items: [
      {
        item_id: "b".repeat(64),
        name: "review",
        description: "Review",
        source_id: "project",
        logical_path: "/skills/review",
      },
    ],
  };
  const authored = "中文😀 $review $unknown $review";
  const preview = previewInput("input-test", [authored], new Map(), catalog);
  const saved = [
    ...JSON.parse(JSON.stringify(preview)),
    {
      kind: "user",
      text: '<skill-selection source="harness-ui">Read Skills</skill-selection>',
      metadata: { display: false },
    },
  ];
  for (const parts of [preview, saved]) {
    const view = render(
      <InputContent parts={parts} renderText={(text) => text} />,
    );
    expect(
      view.container.querySelectorAll('[data-skill="review"]'),
    ).toHaveLength(2);
    expect(view.container.textContent).not.toContain("Read Skills");
    expect(inputCopyText(parts)).toBe(authored);
    view.unmount();
  }
  const legacy = render(
    <InputContent
      parts={[{ kind: "user", text: authored }]}
      renderText={(text) => text}
    />,
  );
  expect(legacy.container.querySelector("[data-skill]")).toBeNull();
});
