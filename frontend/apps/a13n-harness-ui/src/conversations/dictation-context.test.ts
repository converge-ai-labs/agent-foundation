// @vitest-environment jsdom
import { expect, it } from "vitest";
import { dictationContext, dictationSnapshot } from "./dictation-context";
it("budgets words around the selected text and includes only authored chat prose", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<p data-dictation-human>older human question</p><div>tool secret</div><p data-reply-source="answer">Use Pyright again</p><p data-dictation-human>明天继续测试</p>';
  const quote = { source: "answer", start: 4, raw: "Pyright", text: "Pyright" };
  const context = dictationContext(
    { reader: { current: reader }, words: 2 },
    quote,
  );
  expect(context).toContain("Human: question");
  expect(context).toContain("Selected assistant text: Pyright");
  expect(context).toContain("Assistant: again");
  expect(context).toContain("Human: 明天");
  expect(context).not.toMatch(/older|secret|继续/);
});

it("prioritizes draft words over nearby prose and retains the selected span", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<p data-reply-source="answer">Earlier Pyright later words</p>';
  const quote = { source: "answer", start: 8, raw: "Pyright", text: "Pyright" };
  const snapshot = dictationSnapshot(
    { reader: { current: reader }, words: 2 },
    quote,
    {
      before: "old please run ",
      selected: "Mypy",
      after: " now please later",
    },
  );
  expect(snapshot.draft).toEqual({
    before: "please run ",
    selected: "Mypy",
    after: " now please",
  });
  expect(snapshot.context).toBe("Selected assistant text: Pyright");
});
it("zero budget removes surrounding draft while preserving selected text", () => {
  expect(
    dictationSnapshot({ words: 0 }, undefined, {
      before: "before",
      selected: "selected",
      after: "after",
    }),
  ).toEqual({
    draft: { before: "", selected: "selected", after: "" },
    context: "",
  });
});
