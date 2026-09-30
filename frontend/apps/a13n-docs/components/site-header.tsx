"use client";
import { Moon, Sun, Translate } from "@phosphor-icons/react/dist/ssr";
import Link from "fumadocs-core/link";
import {
  FullSearchTrigger,
  SearchTrigger,
} from "fumadocs-ui/layouts/shared/slots/search-trigger";
import { useTheme } from "next-themes";
import type { ComponentProps, ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useLocale } from "./provider";
import { localeUrl, messages, type Locale } from "@/lib/i18n";
import { site } from "@/lib/site";
import { NavTitle } from "./brand";

export interface SiteTab {
  title: ReactNode;
  url: string;
  active?: boolean;
}

export const iconButton =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-fd-muted-foreground transition-colors duration-150 hover:bg-fd-accent hover:text-fd-foreground [&_svg]:size-[18px]";

/**
 * One header row for every page: brand, section tabs, search, theme, and GitHub.
 * Its backdrop and hairline extend to the viewport edges; the page shell clips them.
 * The row spans the docs layout width everywhere, so the brand and actions stay in place across pages.
 */
export function SiteHeader({
  tabs,
  menu,
  className = "",
  ...props
}: ComponentProps<"header"> & { tabs: SiteTab[]; menu?: ReactNode }) {
  const locale = useLocale();
  const t = messages[locale];
  return (
    <header
      {...props}
      className={`z-10 h-14 before:absolute before:inset-y-0 before:-inset-x-[50vw] before:-z-10 before:border-b before:bg-fd-background/85 before:backdrop-blur-md ${className}`}
    >
      <div className="mx-auto flex h-full max-w-(--fd-layout-width,97rem) items-center gap-8 px-4 md:px-6">
        <Link
          href={localeUrl("/", locale)}
          aria-label={t.home}
          className="shrink-0"
        >
          <NavTitle />
        </Link>
        <nav className="flex h-full gap-6 max-lg:hidden">
          {tabs.map((tab) => (
            <Link
              key={tab.url}
              href={tab.url}
              aria-current={tab.active ? "page" : undefined}
              className="relative inline-flex items-center text-sm font-medium whitespace-nowrap text-fd-muted-foreground transition-colors duration-150 after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:rounded-full hover:text-fd-foreground aria-[current=page]:text-fd-foreground aria-[current=page]:after:bg-fd-foreground"
            >
              {tab.title}
            </Link>
          ))}
        </nav>
        <div className="ms-auto flex items-center gap-1">
          <FullSearchTrigger
            hideIfDisabled
            className="me-2 h-8 w-56 rounded-[10px] border-(--a13n-input-border) bg-transparent py-0 ps-2.5 pe-1.5 text-[13px] hover:bg-fd-accent max-md:hidden"
          />
          <SearchTrigger hideIfDisabled className={`${iconButton} md:hidden`} />
          <LanguageSwitch />
          <ThemeToggle />
          <a
            href={site.repository}
            aria-label={t.github}
            className={`${iconButton} max-md:hidden`}
          >
            <GitHubMark />
          </a>
          {menu}
        </div>
      </div>
    </header>
  );
}

/** GitHub's own mark (Octicons `mark-github`). */
export function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  );
}

function ThemeToggle() {
  const t = messages[useLocale()];
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <button
      type="button"
      aria-label={t.theme}
      className={iconButton}
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Moon className="dark:hidden" />
      <Sun className="hidden dark:block" />
    </button>
  );
}

function LanguageSwitch() {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  return (
    <label className="relative inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[13px] text-fd-muted-foreground hover:bg-fd-accent hover:text-fd-foreground">
      <Translate className="size-[18px]" aria-hidden="true" />
      <select
        aria-label={messages[locale].language}
        value={locale}
        className="cursor-pointer appearance-none bg-transparent pe-1 outline-none focus-visible:ring-2 focus-visible:ring-fd-ring"
        onChange={(event) =>
          router.push(
            `${localeUrl(pathname, event.target.value as Locale)}${window.location.search}${window.location.hash}`,
          )
        }
      >
        <option value="en">English</option>
        <option value="zh-CN">简体中文</option>
      </select>
    </label>
  );
}
