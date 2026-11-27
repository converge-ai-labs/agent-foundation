// Completion acknowledgements concern the saved transcript actually on screen,
// not a selected route, live stream, or an in-progress smooth scroll.
export function savedResultVisible(element: HTMLElement): boolean {
  if (document.visibilityState !== "visible" || !document.hasFocus())
    return false;
  if (
    !element.isConnected ||
    !element.getClientRects().length ||
    element.clientHeight <= 0
  )
    return false;
  const rect = element.getBoundingClientRect();
  if (
    rect.bottom <= 0 ||
    rect.top >= window.innerHeight ||
    rect.right <= 0 ||
    rect.left >= window.innerWidth
  )
    return false;
  if (element.closest("[hidden], [inert], [aria-hidden='true']")) return false;
  if (
    typeof element.checkVisibility === "function" &&
    !element.checkVisibility({
      visibilityProperty: true,
      opacityProperty: true,
    })
  )
    return false;
  return element.scrollHeight - element.scrollTop - element.clientHeight <= 1;
}

export function observeSavedResult(element: HTMLElement, visible: () => void) {
  const check = () => {
    if (savedResultVisible(element)) visible();
  };
  // Observe the committed layout, not a selected route or pending scroll.
  const frame = requestAnimationFrame(check);
  const observer =
    typeof ResizeObserver === "undefined"
      ? undefined
      : new ResizeObserver(check);
  observer?.observe(element);
  if (element.firstElementChild) observer?.observe(element.firstElementChild);
  element.addEventListener("scroll", check);
  window.addEventListener("focus", check);
  window.addEventListener("resize", check);
  document.addEventListener("visibilitychange", check);
  return () => {
    cancelAnimationFrame(frame);
    observer?.disconnect();
    element.removeEventListener("scroll", check);
    window.removeEventListener("focus", check);
    window.removeEventListener("resize", check);
    document.removeEventListener("visibilitychange", check);
  };
}
