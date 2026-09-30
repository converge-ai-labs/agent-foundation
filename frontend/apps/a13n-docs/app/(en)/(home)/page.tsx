import { HomePage } from "@/components/home-page";
import { pageMetadata } from "@/components/documentation-page";
export default function Page() {
  return <HomePage locale="en" />;
}
export const generateMetadata = () => pageMetadata([], "en");
