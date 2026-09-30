import { ArrowRight, CaretRight } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import type { ReactNode } from "react";
import { type Locale, localeUrl, messages } from "@/lib/i18n";
import { icon } from "@/lib/icons";

export function Intro({
  locale = "en",
  title,
  action,
  children,
}: {
  title: string;
  locale?: Locale;
  action: { text: string; href: string };
  children: ReactNode;
}) {
  return (
    <header className="pt-16 pb-12 md:pt-20">
      <h1 className="text-[32px] leading-tight font-medium tracking-[-0.02em] text-balance">
        {title}
      </h1>
      <div className="mt-3 max-w-[600px] text-base leading-relaxed text-pretty text-fd-muted-foreground [&_p]:m-0">
        {children}
      </div>
      <Link
        href={localeUrl(action.href, locale)}
        className="mt-7 inline-flex h-9 items-center gap-1.5 rounded-lg bg-fd-primary px-3.5 text-sm font-medium text-fd-primary-foreground transition-opacity duration-150 hover:opacity-90"
      >
        {action.text}
        <ArrowRight className="size-4" />
      </Link>
    </header>
  );
}

export function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mt-12 first-of-type:mt-0">
      <h2 className="mb-3 text-[15px] font-medium">{title}</h2>
      {children}
    </section>
  );
}

export function Products({ children }: { children: ReactNode }) {
  return <div className="grid gap-3 md:grid-cols-3">{children}</div>;
}

export function Product({
  locale = "en",
  icon: name,
  title,
  audience,
  href,
  start,
  children,
}: {
  locale?: Locale;
  icon: string;
  title: string;
  audience: string;
  href: string;
  start: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-xl bg-(--a13n-surface) p-5">
      <div className="flex size-9 items-center justify-center rounded-lg bg-fd-background [&_svg]:size-5">
        {icon(name)}
      </div>
      <h3 className="mt-4 text-[15px] font-medium">{title}</h3>
      <p className="text-[13px] text-fd-muted-foreground">{audience}</p>
      <div className="mt-2.5 mb-5 text-sm leading-relaxed text-(--a13n-text-supporting) [&_p]:m-0">
        {children}
      </div>
      <div className="mt-auto flex items-center gap-4 text-[13px] font-medium">
        <Link
          href={localeUrl(start, locale)}
          className="inline-flex items-center gap-1 hover:underline hover:underline-offset-4"
        >
          {messages[locale].getStarted}
          <ArrowRight className="size-3.5" />
        </Link>
        <Link
          href={localeUrl(href, locale)}
          className="text-fd-muted-foreground transition-colors duration-150 hover:text-fd-foreground"
        >
          {messages[locale].overview}
        </Link>
      </div>
    </div>
  );
}

/** A surface of link rows, two columns wide on larger screens. */
export function Rows({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-0.5 rounded-xl bg-(--a13n-surface) p-1.5 md:grid-cols-2">
      {children}
    </div>
  );
}

export function Row({
  locale = "en",
  title,
  href,
  children,
}: {
  title: string;
  href: string;
  locale?: Locale;
  children: ReactNode;
}) {
  return (
    <Link
      href={localeUrl(href, locale)}
      className="group flex items-center gap-3 rounded-[10px] px-3.5 py-3 transition-colors duration-150 hover:bg-fd-accent"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-[13px] text-fd-muted-foreground">
          {children}
        </span>
      </span>
      <CaretRight className="size-4 shrink-0 text-fd-muted-foreground transition-colors duration-150 group-hover:text-fd-foreground" />
    </Link>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <div className="mt-12 space-y-2 text-[13px] leading-relaxed text-fd-muted-foreground [&_a]:font-medium [&_a]:text-fd-foreground [&_a:hover]:underline [&_a:hover]:underline-offset-4 [&_code]:font-mono [&_code]:text-[12px] [&_p]:m-0">
      {children}
    </div>
  );
}
