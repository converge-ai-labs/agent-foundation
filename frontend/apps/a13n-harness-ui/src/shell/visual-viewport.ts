// Mobile browsers can keep the layout viewport behind the software keyboard.
// Fit the workbench (not just an overlay) to the visible viewport so its reader
// and composer keep their normal flex layout and remain reachable together.
export function fitVisualViewport(element: HTMLElement) {
  const viewport = window.visualViewport;
  if (!viewport) return () => {};
  let frame = 0;
  const update = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      if (window.innerWidth <= 700 && viewport.scale === 1) {
        element.style.setProperty("--visible-height", `${viewport.height}px`);
        element.style.setProperty("--visible-top", `${viewport.offsetTop}px`);
      } else {
        element.style.removeProperty("--visible-height");
        element.style.removeProperty("--visible-top");
      }
    });
  };
  update();
  viewport.addEventListener("resize", update);
  viewport.addEventListener("scroll", update);
  window.addEventListener("resize", update);
  return () => {
    cancelAnimationFrame(frame);
    viewport.removeEventListener("resize", update);
    viewport.removeEventListener("scroll", update);
    window.removeEventListener("resize", update);
    element.style.removeProperty("--visible-height");
    element.style.removeProperty("--visible-top");
  };
}
