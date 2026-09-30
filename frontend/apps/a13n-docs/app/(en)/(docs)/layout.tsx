import { DocsShell } from "@/components/docs-shell";
export default function Layout({ children }: LayoutProps<"/">) {
  return <DocsShell locale="en">{children}</DocsShell>;
}
