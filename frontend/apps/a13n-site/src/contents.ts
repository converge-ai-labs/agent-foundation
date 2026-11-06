/** Keep the section navigation in sync with reading and native fragment links. */
export function mountContents() {
  const links = Array.from(
    document.querySelectorAll<HTMLAnchorElement>("[data-toc] a"),
  );
  if (!links.length) return;
  const targets = new Set(
    links.map((link) => decodeURIComponent(link.hash.slice(1))),
  );
  const headings = Array.from(
    document.querySelectorAll<HTMLElement>(".prose h2[id], .prose h3[id]"),
  ).filter((heading) => targets.has(heading.id));
  let current: string | undefined;
  let scheduled = false;

  const update = () => {
    scheduled = false;
    let active = headings[0]?.id;
    for (const heading of headings) {
      if (heading.getBoundingClientRect().top > 112) break;
      active = heading.id;
    }
    if (active === current) return;
    current = active;
    for (const link of links) {
      if (decodeURIComponent(link.hash.slice(1)) === active)
        link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
  };
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(update);
  };
  for (const link of links) {
    link.addEventListener("click", () => {
      // Collapse before the browser resolves the fragment's new position.
      link.closest("details")?.removeAttribute("open");
    });
  }
  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", schedule);
  addEventListener("hashchange", schedule);
  document.fonts.ready.then(schedule);
  update();
}
