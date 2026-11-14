// State changes immediately; only a noninteractive snapshot remains during exit.
// Capture current visual positions before cancelling so repeated closes stay continuous.
export function animateTabClose(
  strip: HTMLElement | null,
  id: string,
  update: () => void,
) {
  if (!strip || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    update();
    return;
  }
  const motion = getComputedStyle(strip);
  const exitDuration =
    parseFloat(motion.getPropertyValue("--a13n-duration-fast")) || 120;
  const moveDuration =
    parseFloat(motion.getPropertyValue("--a13n-duration-base")) || 200;
  const exitEase =
    motion.getPropertyValue("--a13n-ease-in").trim() ||
    "cubic-bezier(.4,0,1,1)";
  const moveEase =
    motion.getPropertyValue("--a13n-ease-out").trim() ||
    "cubic-bezier(.22,1,.36,1)";
  const tabs = Array.from(strip.querySelectorAll<HTMLElement>("[data-tab-id]"));
  const positions = new Map(
    tabs.map((tab) => [tab.dataset.tabId, tab.getBoundingClientRect()]),
  );
  const closing = tabs.find((tab) => tab.dataset.tabId === id);
  const rect = closing?.getBoundingClientRect();
  const ghost = closing?.cloneNode(true) as HTMLElement | undefined;
  if (ghost && rect && typeof ghost.animate === "function") {
    ghost.removeAttribute("data-tab-id");
    ghost
      .querySelectorAll("[id]")
      .forEach((node) => node.removeAttribute("id"));
    ghost.setAttribute("aria-hidden", "true");
    ghost.inert = true;
    Object.assign(ghost.style, {
      position: "fixed",
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      margin: "0",
      pointerEvents: "none",
      zIndex: "60",
      animation: "none",
    });
    document.body.append(ghost);
    const exit = ghost.animate(
      [
        { opacity: 1, transform: "scale(1)" },
        { opacity: 0, transform: "translateY(-4px) scale(.96)" },
      ],
      { duration: exitDuration, easing: exitEase },
    );
    void exit.finished.then(
      () => ghost.remove(),
      () => ghost.remove(),
    );
  }
  tabs.forEach((tab) =>
    tab.getAnimations?.().forEach((animation) => animation.cancel()),
  );
  update();
  strip.querySelectorAll<HTMLElement>("[data-tab-id]").forEach((tab) => {
    const before = positions.get(tab.dataset.tabId);
    if (!before || typeof tab.animate !== "function") return;
    const after = tab.getBoundingClientRect();
    const delta = before.left - after.left;
    if (Math.abs(delta) < 1) return;
    tab.animate(
      [{ transform: `translateX(${delta}px)` }, { transform: "translateX(0)" }],
      {
        duration: moveDuration,
        easing: moveEase,
      },
    );
  });
}
