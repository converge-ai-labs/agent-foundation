import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useMediaQuery } from "../src/hooks/use-media-query";

afterEach(() => vi.unstubAllGlobals());

it("keeps mobile and desktop queries complementary at Tailwind's 768px boundary while resizing", () => {
  let width = 798;
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", (query: string) => {
    const match = /^\((min-width:|max-width:|width <) (\d+)px\)$/.exec(query);
    if (!match) throw new Error(`Unexpected media query: ${query}`);
    const [, comparison, boundary] = match;
    return {
      media: query,
      get matches() {
        if (comparison === "min-width:") return width >= Number(boundary);
        if (comparison === "max-width:") return width <= Number(boundary);
        return width < Number(boundary);
      },
      addEventListener: (_event: string, listener: () => void) =>
        listeners.add(listener),
      removeEventListener: (_event: string, listener: () => void) =>
        listeners.delete(listener),
    };
  });
  const { result } = renderHook(() => ({
    mobile: useMediaQuery("max-md"),
    desktop: useMediaQuery("md"),
  }));
  expect(result.current).toEqual({ mobile: false, desktop: true });

  for (const [nextWidth, mobile] of [
    [767, true],
    [767.5, true],
    [768, false],
    [799, false],
    [800, false],
    [390, true],
  ] as const) {
    act(() => {
      width = nextWidth;
      listeners.forEach((listener) => listener());
    });
    expect(result.current).toEqual({ mobile, desktop: !mobile });
  }
});
