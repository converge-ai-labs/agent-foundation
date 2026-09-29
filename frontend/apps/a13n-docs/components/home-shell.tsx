import type { ReactNode } from "react";
import { sectionLinks } from "@/lib/layout.shared";
import { SiteHeader } from "./site-header";

/** The shell for pages outside the docs tree: the site header over one centered column. */
export function HomeShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col overflow-x-clip">
      <SiteHeader tabs={sectionLinks()} className="sticky top-0" />
      <main className="mx-auto flex w-full max-w-[960px] flex-1 flex-col px-5 pb-24 md:px-8">
        {children}
      </main>
    </div>
  );
}
