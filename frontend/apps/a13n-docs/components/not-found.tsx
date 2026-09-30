import { type Locale, messages } from "@/lib/i18n";
import Link from "next/link";
import { HomeShell } from "@/components/home-shell";
import { sectionLinks } from "@/lib/layout.shared";

export function NotFoundPage({ locale }: { locale: Locale }) {
  const t = messages[locale];
  return (
    <HomeShell locale={locale}>
      <div className="my-auto py-24 text-center">
        <h1 className="text-[22px] font-medium">{t.notFound}</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-pretty text-fd-muted-foreground">
          {t.notFoundDescription}
        </p>
        <nav className="mt-7 flex flex-wrap justify-center gap-1.5">
          {sectionLinks(locale).map((link) => (
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
