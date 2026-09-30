import { HomeShell } from "@/components/home-shell";

export default function Layout({ children }: LayoutProps<"/zh-CN">) {
  return <HomeShell locale="zh-CN">{children}</HomeShell>;
}
