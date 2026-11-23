// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { createRef } from "react";
import { ReaderFades } from "./reader-fades";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("exposes the reader's scrollbar width to its fades", () => {
  let notify = () => {};
  const disconnect = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    vi.fn(function (callback: () => void) {
      notify = callback;
      return { observe: vi.fn(), disconnect };
    }),
  );
  const reader = createRef<HTMLDivElement>();
  const view = render(
    <>
      <ReaderFades reader={reader} />
      <div ref={reader} />
    </>,
  );
  const element = reader.current!;
  const fades = view.container.querySelectorAll<HTMLElement>("[data-edge]");
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

it("leaves the fades on their default insets without a reader", () => {
  const view = render(<ReaderFades />);
  for (const fade of view.container.querySelectorAll<HTMLElement>(
    "[data-edge]",
  ))
    expect(fade.style.getPropertyValue("--reader-scrollbar")).toBe("");
});
