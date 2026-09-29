import { getLayoutTabs } from "fumadocs-ui/layouts/shared";
import { source } from "./source";

/** The docs sections, in navigation order. */
export function sectionLinks() {
  return getLayoutTabs(source.getPageTree()).map((tab) => ({
    title: tab.title,
    url: tab.url,
  }));
}
