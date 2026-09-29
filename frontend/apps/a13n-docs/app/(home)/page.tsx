import { notFound } from "next/navigation";
import * as home from "@/components/home";
import { getMDXComponents } from "@/components/mdx";
import { relativeLink } from "@/lib/relative-link";
import { source } from "@/lib/source";

export default function HomePage() {
  const page = source.getPage([]);
  if (!page || page.type !== "docs") notFound();
  const MDX = page.data.body;

  return (
    <MDX components={getMDXComponents({ ...home, a: relativeLink(page) })} />
  );
}
