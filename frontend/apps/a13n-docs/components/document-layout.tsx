import type { Metadata, Viewport } from "next";
import { logo } from "@/components/brand";
import { Provider } from "@/components/provider";
import { site } from "@/lib/site";
import "@/app/global.css";
import type { ReactNode } from "react";
import { type Locale } from "@/lib/i18n";

export const baseMetadata: Metadata = {
  metadataBase: new URL(site.url),
  title: site.name,
  description: site.description,
  icons: { icon: { url: logo.src, type: "image/svg+xml" } },
  openGraph: { siteName: site.name, type: "website" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#151515" },
  ],
};

export function DocumentLayout({
  children,
  locale,
}: {
  children: ReactNode;
  locale: Locale;
}) {
  return (
    <html lang={locale} suppressHydrationWarning>
      <body className="flex min-h-screen flex-col">
        <Provider locale={locale}>{children}</Provider>
      </body>
    </html>
  );
}
