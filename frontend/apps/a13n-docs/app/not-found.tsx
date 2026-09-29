import type { Metadata } from "next";
import Link from "next/link";
import { HomeShell } from "@/components/home-shell";
import { sectionLinks } from "@/lib/layout.shared";

export const metadata: Metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <HomeShell>
      <div className="my-auto py-24 text-center">
        <h1 className="text-[22px] font-medium">Page not found</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-pretty text-fd-muted-foreground">
          This page does not exist or has moved. Start again from a section
          below, or search the docs.
        </p>
        <nav className="mt-7 flex flex-wrap justify-center gap-1.5">
          {sectionLinks().map((link) => (
            <Link
              key={link.url}
              href={link.url}
              className="inline-flex h-8 items-center rounded-lg bg-(--a13n-surface) px-3 text-[13px] font-medium transition-colors duration-150 hover:bg-fd-foreground/8"
            >
              {link.title}
            </Link>
          ))}
        </nav>
      </div>
    </HomeShell>
  );
}
