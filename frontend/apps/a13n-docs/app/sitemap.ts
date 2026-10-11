import type { MetadataRoute } from "next";
import { i18n } from "@/lib/i18n";
import { site } from "@/lib/site";
import { source } from "@/lib/source";

export const revalidate = false;

/** Every page in every language, at the canonical URL its metadata declares. */
export default function sitemap(): MetadataRoute.Sitemap {
  return i18n.languages.flatMap((locale) =>
    source.getPages(locale).map((page) => ({
      url: `${site.url}${page.url.replace(/\/?$/, "/")}`,
    })),
  );
}
