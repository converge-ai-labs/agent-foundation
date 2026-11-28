// @vitest-environment jsdom
import { expect, it } from "vitest";
import { selectedQuote, quoteRange } from "./reply-quote";

it("restores the selected occurrence across formatted text and paragraphs without attaching to different text", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<div data-reply-source="one"><p>同一句。同一句。<b>开场</b></p><p>然后呢？</p></div><p>user input</p>';
  document.body.append(reader);
  const source = reader.firstElementChild as HTMLElement;
  const range = document.createRange();
  range.setStart(source.querySelector("p")!.firstChild!, 4);
  range.setEnd(source.lastElementChild!.firstChild!, 2);
  const selection = window.getSelection()!;
  try {
    selection.removeAllRanges();
    selection.addRange(range);
    const { quote } = selectedQuote(reader, selection)!;
    source.innerHTML = source.innerHTML; // A Markdown rerender replaces the text nodes.
    const recovered = quoteRange(source, quote)!;
    expect(recovered.startOffset).toBe(4);
    expect(recovered.toString()).toBe("同一句。开场然后");
    source.querySelector("b")!.textContent = "新词";
    expect(quoteRange(source, quote)).toBeUndefined();
    range.selectNodeContents(reader);
    selection.removeAllRanges();
    selection.addRange(range);
    expect(selectedQuote(reader, selection)).toBeUndefined();
  } finally {
    selection.removeAllRanges();
    reader.remove();
  }
});

it("ignores citation chips and the Sources list when capturing and restoring a reply quote", () => {
  const reader = document.createElement("div");
  reader.innerHTML =
    '<div data-reply-source="one"><p>First<span data-reply-decoration>[1]</span> and second.</p><details data-reply-decoration><summary>Sources</summary>Evidence</details></div>';
  document.body.append(reader);
  const source = reader.firstElementChild as HTMLElement;
  const prose = source.querySelector("p")!;
  const selection = window.getSelection()!;
  const range = document.createRange();
  range.setStart(prose.firstChild!, 0);
  range.setEnd(prose.lastChild!, 12);
  try {
    selection.removeAllRanges();
    selection.addRange(range);
    const { quote } = selectedQuote(reader, selection)!;
    expect(quote.text).toBe("First and second.");
    source.querySelector("[data-reply-decoration]")!.textContent = "[2]";
    const restored = quoteRange(source, quote)!;
    expect(restored.startOffset).toBe(0);
    expect(restored.endOffset).toBe(12);
  } finally {
    selection.removeAllRanges();
    reader.remove();
  }
});
