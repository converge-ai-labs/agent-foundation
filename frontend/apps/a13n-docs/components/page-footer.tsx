"use client";
import { useLocale } from "./provider";
import { messages } from "@/lib/i18n";
import { CaretLeft, CaretRight } from "@phosphor-icons/react/dist/ssr";
import { usePathname } from "fumadocs-core/framework";
import Link from "fumadocs-core/link";
import type * as PageTree from "fumadocs-core/page-tree";
import { useFooterItems } from "fumadocs-ui/utils/use-footer-items";

const trimSlash = (url: string) => url.replace(/\/$/, "");

/** Previous and next pages, set apart from the content by whitespace. */
export function PageFooter() {
  const t = messages[useLocale()];
  const items = useFooterItems();
  const pathname = trimSlash(usePathname());
  const index = items.findIndex((item) => trimSlash(item.url) === pathname);
  if (index === -1) return null;
  const previous = items[index - 1];
  const next = items[index + 1];

  return (
    <nav aria-label={t.pages} className="mt-12 grid gap-3 sm:grid-cols-2">
      {previous && <FooterLink item={previous} direction="previous" />}
      {next && <FooterLink item={next} direction="next" />}
    </nav>
  );
}

function FooterLink({
  item,
  direction,
}: {
  item: PageTree.Item;
  direction: "previous" | "next";
}) {
  const t = messages[useLocale()];
  const isNext = direction === "next";
  const Caret = isNext ? CaretRight : CaretLeft;
  return (
    <Link
      href={item.url}
      className={`group flex flex-col gap-0.5 rounded-xl bg-(--a13n-surface) px-4 py-3 transition-colors duration-150 hover:bg-fd-foreground/7 ${isNext ? "items-end text-end sm:col-start-2" : ""}`}
    >
      <span className="text-xs text-fd-muted-foreground">
        {isNext ? t.next : t.previous}
      </span>
      <span
        className={`flex items-center gap-1 text-sm font-medium ${isNext ? "flex-row-reverse" : ""}`}
      >
        <Caret className="size-3.5 text-fd-muted-foreground transition-colors duration-150 group-hover:text-fd-foreground" />
        <span>{item.name}</span>
      </span>
    </Link>
  );
}
