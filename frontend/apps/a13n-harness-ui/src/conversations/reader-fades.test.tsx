// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { createRef } from "react";
import { ReaderFades } from "./reader-fades";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps both fade edges and accounts for an attached reader's scrollbar", () => {
  let notify = () => {};
  const disconnect = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    vi.fn(function (callback: () => void) {
      notify = callback;
      return { observe: vi.fn(), disconnect };
    }),
  );
  const fixed = render(<ReaderFades />);
  const fixedFades =
    fixed.container.querySelectorAll<HTMLElement>("[data-edge]");
  expect(Array.from(fixedFades, (fade) => fade.dataset.edge)).toEqual([
    "top",
    "bottom",
  ]);

  const reader = createRef<HTMLDivElement>();
  const view = render(
    <>
      <ReaderFades reader={reader} />
      <div ref={reader} />
    </>,
  );
  const element = reader.current!;
  const fades = view.container.querySelectorAll<HTMLElement>("[data-edge]");
  expect(Array.from(fades, (fade) => fade.dataset.edge)).toEqual([
    "top",
    "bottom",
  ]);
  Object.defineProperty(element, "offsetWidth", { value: 366 });
  const width = (client: number) => {
    Object.defineProperty(element, "clientWidth", {
      value: client,
      configurable: true,
    });
    act(notify);
  };
  // A classic scrollbar takes its width from the text column.
  width(360);
  for (const fade of fades)
    expect(fade.style.getPropertyValue("--reader-scrollbar")).toBe("6px");
  // An overlay scrollbar takes none.
  width(366);
  for (const fade of fades)
    expect(fade.style.getPropertyValue("--reader-scrollbar")).toBe("0px");
  view.unmount();
  expect(disconnect).toHaveBeenCalledTimes(1);
});
