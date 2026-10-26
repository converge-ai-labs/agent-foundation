import { act } from "@testing-library/react";
import { beforeEach, vi } from "vitest";

/** jsdom has no layout; drive the browser's visibility notifications explicitly. */
export function mockIntersections() {
  const callbacks = new Map<Element, (visible: boolean) => void>();
  beforeEach(() => {
    callbacks.clear();
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        private targets = new Set<Element>();
        constructor(private callback: IntersectionObserverCallback) {}
        observe(target: Element) {
          this.targets.add(target);
          callbacks.set(target, (isIntersecting) =>
            this.callback(
              [{ target, isIntersecting } as IntersectionObserverEntry],
              this as unknown as IntersectionObserver,
            ),
          );
        }
        disconnect() {
          for (const target of this.targets) callbacks.delete(target);
        }
      },
    );
  });
  return (target: Element, visible: boolean) =>
    act(() => {
      const callback = callbacks.get(target);
      if (!callback) throw new Error("Element is not observed");
      callback(visible);
    });
}
