import type { ComponentProps } from "react";
import { type Locale } from "@/lib/i18n";
import { notFound } from "next/navigation";
import * as home from "@/components/home";
import { getMDXComponents } from "@/components/mdx";
import { relativeLink } from "@/lib/relative-link";
import { source } from "@/lib/source";

export function HomePage({ locale }: { locale: Locale }) {
  const page = source.getPage([], locale);
  if (!page || page.type !== "docs") notFound();
  const MDX = page.data.body;

  return (
    <MDX
      components={getMDXComponents(
        {
          ...home,
          Intro: (props: ComponentProps<typeof home.Intro>) => (
            <home.Intro {...props} locale={locale} />
          ),
          Product: (props: ComponentProps<typeof home.Product>) => (
            <home.Product {...props} locale={locale} />
          ),
          Row: (props: ComponentProps<typeof home.Row>) => (
            <home.Row {...props} locale={locale} />
          ),
          a: relativeLink(page),
        },
        locale,
      )}
    />
  );
}
