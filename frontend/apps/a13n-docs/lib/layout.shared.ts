import { getLayoutTabs } from "fumadocs-ui/layouts/shared";
import type { Locale } from "./i18n";
import { source } from "./source";

/** The docs sections, in navigation order. */
export function sectionLinks(locale: Locale = "en") {
  return getLayoutTabs(source.getPageTree(locale)).map((tab) => ({
    title: tab.title,
    url: tab.url,
  }));
}
