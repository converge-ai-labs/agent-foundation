import { notFound } from "next/navigation";
import { markdownSegments } from "@/lib/site";
import { docsLlms, source } from "@/lib/source";

export const revalidate = false;

export async function GET(
  _request: Request,
  { params }: RouteContext<"/md/[...slug]">,
) {
  const { slug } = await params;
  const last = slug.at(-1)?.replace(/\.md$/, "");
  const slugs = [...slug.slice(0, -1), last].filter(
    (part) => part && part !== "index",
  ) as string[];
  const page = source.getPage(slugs);
  if (!page || page.type !== "docs") notFound();

  return new Response(await docsLlms.page(page), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}

export function generateStaticParams() {
  return source
    .getPages("en")
    .filter((page) => page.type === "docs")
    .map((page) => ({ slug: markdownSegments(page.slugs) }));
}
