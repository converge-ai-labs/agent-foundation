/** Recognize Console resource destinations, never labels supplied by Markdown. */
export function markdownResourceLink(
  href: string | undefined,
  pageUrl: string,
) {
  if (!href || href.startsWith("#") || href.startsWith("?")) return null;
  let url: URL;
  try {
    url = new URL(href, pageUrl);
    const page = new URL(pageUrl);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.origin !== page.origin ||
      url.username ||
      url.password
    )
      return null;
  } catch {
    return null;
  }

  const agent = url.pathname.match(/^\/workspace\/[^/]+\/agents\/([^/]+)\/?$/);
  const run =
    /^\/workspace\/[^/]+\/sessions\/[^/]+\/threads\/[^/]+\/runs\/[^/]+\/?$/.test(
      url.pathname,
    );
  const kind = agent && agent[1] !== "new" ? "agent" : run ? "run" : null;
  return kind ? { kind, to: url.pathname + url.search + url.hash } : null;
}
