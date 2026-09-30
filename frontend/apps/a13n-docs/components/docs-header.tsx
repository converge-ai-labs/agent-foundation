"use client";
import { useLocale } from "./provider";
import { messages } from "@/lib/i18n";
import { List } from "@phosphor-icons/react/dist/ssr";
import { usePathname } from "fumadocs-core/framework";
import { useTreePath } from "fumadocs-ui/contexts/tree";
import { useNotebookLayout } from "fumadocs-ui/layouts/notebook";
import { isLayoutTabActive } from "fumadocs-ui/layouts/shared";
import type { ComponentProps } from "react";
import { iconButton, SiteHeader } from "./site-header";

/** The notebook layout's header slot, with the current section tab marked. */
export function DocsHeader(props: ComponentProps<"header">) {
  const t = messages[useLocale()];
  const {
    slots,
    props: { tabs },
  } = useNotebookLayout();
  const path = useTreePath();
  const pathname = usePathname();
  const active = tabs.findLastIndex((tab) =>
    isLayoutTabActive(tab, path, pathname),
  );
  const Trigger = slots.sidebar.trigger;

  return (
    <SiteHeader
      {...props}
      className="sticky top-(--fd-docs-row-1) [grid-area:header] layout:[--fd-header-height:--spacing(14)]"
      tabs={tabs
        .filter((tab, i) => !tab.unlisted || i === active)
        .map((tab) => ({
          title: tab.title,
          url: tab.url,
          active: tab === tabs[active],
        }))}
      menu={
        <Trigger
          aria-label={t.navigation}
          className={`${iconButton} -me-1.5 md:hidden`}
        >
          <List />
        </Trigger>
      }
    />
  );
}
