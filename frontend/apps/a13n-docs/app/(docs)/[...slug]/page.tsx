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
import { source } from "@/lib/source";

const slots = { footer: PageFooter };

export default async function Page(props: PageProps<"/[...slug]">) {
  const { slug } = await props.params;
  const page = source.getPage(slug);
  if (!page) notFound();

  if (page.type === "openapi") {
    return (
      <DocsPage full slots={slots} className="pb-16">
        <DocsTitle>{page.data.title}</DocsTitle>
        <DocsDescription className="text-base">
          {page.data.description}
        </DocsDescription>
        <DocsBody>
          <APIPage {...withPageDocument(page.data.getOpenAPIPageProps())} />
        </DocsBody>
      </DocsPage>
    );
  }

  const MDX = page.data.body;
  const markdown = markdownUrl(page.slugs);

  return (
    <DocsPage
      toc={page.data.toc}
      full={page.data.full}
      slots={slots}
      className="pb-16"
    >
      {/* Page actions sit at the title's right; on phones they follow the description. */}
      <header className="grid gap-x-6 gap-y-4 pb-2 md:grid-cols-[1fr_auto]">
        <DocsTitle>{page.data.title}</DocsTitle>
        <div className="flex items-center gap-2 max-md:order-last md:mt-1 md:self-start [&>button]:h-7 [&>button]:rounded-lg [&>button]:border-(--a13n-input-border) [&>button]:bg-transparent">
          <MarkdownCopyButton markdownUrl={markdown} />
          <OpenMenu
            markdownUrl={markdown}
            githubUrl={sourceUrl(`docs/${page.path}`)}
          />
        </div>
        <DocsDescription className="mb-0 text-base md:col-span-2">
          {page.data.description}
        </DocsDescription>
      </header>
      <DocsBody>
        <MDX components={getMDXComponents({ a: relativeLink(page) })} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams().filter(({ slug }) => slug.length > 0);
}

export async function generateMetadata(
  props: PageProps<"/[...slug]">,
): Promise<Metadata> {
  const { slug } = await props.params;
  const page = source.getPage(slug);
  if (!page) notFound();
  return {
    title: page.data.title,
    description: page.data.description,
    alternates: { canonical: page.url },
  };
}
