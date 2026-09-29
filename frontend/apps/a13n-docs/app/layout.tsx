import type { Metadata, Viewport } from "next";
import { logo } from "@/components/brand";
import { Provider } from "@/components/provider";
import { site } from "@/lib/site";
import "./global.css";

export const metadata: Metadata = {
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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex min-h-screen flex-col">
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
