import { createRelativeLink } from "fumadocs-ui/mdx";
import type { ComponentProps } from "react";
import { localeUrl } from "./i18n";
import { type DocsPage, source } from "./source";

// A bare Markdown file link such as `replay.md#errors`, which GitHub also resolves.
const BARE_FILE_LINK = /^[^./#][^:]*\.mdx?(#|$)/;

/** Resolves Markdown file links in a page to site URLs. */
export function relativeLink(page: DocsPage) {
  const RelativeLink = createRelativeLink(source, page);
  return function Link({ href, ...props }: ComponentProps<"a">) {
    return (
      <RelativeLink
        href={
          href && BARE_FILE_LINK.test(href)
            ? `./${href}`
            : href
              ? localeUrl(href, page.locale === "zh-CN" ? "zh-CN" : "en")
              : href
        }
        {...props}
      />
    );
  };
}
