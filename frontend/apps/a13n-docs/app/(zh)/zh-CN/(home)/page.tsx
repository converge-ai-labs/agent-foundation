import { HomePage } from "@/components/home-page";
import { pageMetadata } from "@/components/documentation-page";
export default function Page() {
  return <HomePage locale="zh-CN" />;
}
export const generateMetadata = () => pageMetadata([], "zh-CN");
