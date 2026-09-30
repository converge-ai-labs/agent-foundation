import {
  DocumentLayout,
  baseMetadata,
  viewport,
} from "@/components/document-layout";
export { viewport };
export const metadata = baseMetadata;
export default function Layout({ children }: LayoutProps<"/">) {
  return <DocumentLayout locale="en">{children}</DocumentLayout>;
}
