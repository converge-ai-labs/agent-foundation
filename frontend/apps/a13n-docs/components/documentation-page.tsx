import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
  MarkdownCopyButton,
} from "fumadocs-ui/layouts/notebook/page";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { APIPage } from "@/components/api-page";
import { getMDXComponents } from "@/components/mdx";
import { OpenMenu } from "@/components/page-actions";
import { PageFooter } from "@/components/page-footer";
import { withPageDocument } from "@/lib/openapi-page";
import { markdownUrl, sourceUrl } from "@/lib/site";
import { relativeLink } from "@/lib/relative-link";
import { type Locale, localeUrl } from "@/lib/i18n";
import { source } from "@/lib/source";

const slots = { footer: PageFooter };

export function DocumentationPage({
  slug,
  locale,
}: {
  slug: string[];
  locale: Locale;
}) {
  const page = source.getPage(slug, locale);
  if (!page) notFound();

  if (page.type === "openapi") {
    return (
      <DocsPage full slots={slots} className="pb-16">
        <DocsTitle>{page.data.title}</DocsTitle>
        <DocsDescription>{page.data.description}</DocsDescription>
        <DocsBody>
          <APIPage {...withPageDocument(page.data.getOpenAPIPageProps())} />
        </DocsBody>
      </DocsPage>
    );
  }

  const MDX = page.data.body;
  const markdown = markdownUrl(page.slugs);
  const actionsBesideToc = !page.data.full && page.data.toc.length > 0;
  const actions = (className: string) => (
    <div
      className={`flex items-center gap-2 [&>button]:h-7 [&>button]:rounded-lg [&>button]:border-(--a13n-input-border) [&>button]:bg-transparent ${className}`}
    >
      <MarkdownCopyButton markdownUrl={markdown} />
      <OpenMenu
        markdownUrl={markdown}
        githubUrl={sourceUrl(`docs/${page.path}`)}
      />
    </div>
  );

  return (
    <DocsPage
      toc={page.data.toc}
      full={page.data.full}
      tableOfContent={
        actionsBesideToc ? { footer: actions("mt-6 ms-px") } : undefined
      }
      slots={slots}
      className="pb-16"
    >
      {/* Page actions follow the table of contents when it is shown; otherwise they sit at the
          title's right, and on phones they follow the description. */}
      <header className="grid gap-x-6 gap-y-2 pb-2 md:grid-cols-[1fr_auto]">
        <DocsTitle>{page.data.title}</DocsTitle>
        {actions(
          `max-md:order-last max-md:mt-2 md:mt-1 md:self-start ${actionsBesideToc ? "xl:hidden" : ""}`,
        )}
        <DocsDescription className="mb-0 md:col-span-2">
          {page.data.description}
        </DocsDescription>
      </header>
      <DocsBody>
        <MDX components={getMDXComponents({ a: relativeLink(page) }, locale)} />
      </DocsBody>
    </DocsPage>
  );
}

export function pageMetadata(slug: string[], locale: Locale): Metadata {
  const page = source.getPage(slug, locale);
  if (!page) notFound();
  return {
    title: page.data.title,
    description: page.data.description,
    alternates: {
      canonical: page.url,
      languages: {
        en: localeUrl(page.url, "en"),
        "zh-CN": localeUrl(page.url, "zh-CN"),
        "x-default": localeUrl(page.url, "en"),
      },
    },
    openGraph: {
      locale: locale === "zh-CN" ? "zh_CN" : "en_US",
      alternateLocale: locale === "zh-CN" ? "en_US" : "zh_CN",
    },
  };
}

export function pageParams(locale: Locale) {
  return source
    .getPages(locale)
    .filter((page) => page.slugs.length > 0)
    .map((page) => ({ slug: page.slugs }));
}
