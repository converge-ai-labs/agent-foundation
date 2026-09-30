import {
  DocumentLayout,
  baseMetadata,
  viewport,
} from "@/components/document-layout";
import { messages } from "@/lib/i18n";
export { viewport };
export const metadata = {
  ...baseMetadata,
  description: messages["zh-CN"].description,
};
export default function Layout({ children }: LayoutProps<"/">) {
  return <DocumentLayout locale="zh-CN">{children}</DocumentLayout>;
}
