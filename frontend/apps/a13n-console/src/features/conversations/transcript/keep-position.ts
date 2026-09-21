import { useCallback, useLayoutEffect, useRef } from "react";

interface Pin {
  element: Element;
  top: number;
  viewport: HTMLElement;
}

/**
 * Reading position across a prepend. The caller pins a visible element before
 * asking for earlier content; once that request has settled the element is put
 * back where the reader left it. While the pin is held the scrolling stage is
 * marked `data-loading-earlier`, so the transcript's follow-scroll reads the
 * growth as older content rather than as new output to jump to.
 */
export function useKeepPosition(pending: boolean) {
  const pin = useRef<Pin | null>(null);
  useLayoutEffect(() => {
    const held = pin.current;
    if (pending || !held) return;
    if (held.element.isConnected)
      held.viewport.scrollTop +=
        held.element.getBoundingClientRect().top - held.top;
    delete held.viewport.dataset.loadingEarlier;
    held.viewport.dispatchEvent(new Event("scroll"));
    pin.current = null;
  }, [pending]);
  useLayoutEffect(
    () => () => {
      if (pin.current) delete pin.current.viewport.dataset.loadingEarlier;
    },
    [],
  );
  return useCallback((element?: Element | null) => {
    const viewport = element?.closest("[data-session-stage]");
    if (!element || !(viewport instanceof HTMLElement)) return;
    pin.current = {
      element,
      top: element.getBoundingClientRect().top,
      viewport,
    };
    viewport.dataset.loadingEarlier = "true";
  }, []);
}
