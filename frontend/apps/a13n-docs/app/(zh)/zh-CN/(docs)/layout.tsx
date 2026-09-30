import { DocsShell } from "@/components/docs-shell";
export default function Layout({ children }: LayoutProps<"/zh-CN">) {
  return <DocsShell locale="zh-CN">{children}</DocsShell>;
}
