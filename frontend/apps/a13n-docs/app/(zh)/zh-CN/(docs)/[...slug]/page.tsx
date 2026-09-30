import {
  DocumentationPage,
  pageMetadata,
  pageParams,
} from "@/components/documentation-page";
export default async function Page(props: PageProps<"/zh-CN/[...slug]">) {
  const { slug } = await props.params;
  return <DocumentationPage slug={slug} locale="zh-CN" />;
}
export const generateStaticParams = () => pageParams("zh-CN");
export async function generateMetadata(props: PageProps<"/zh-CN/[...slug]">) {
  return pageMetadata((await props.params).slug, "zh-CN");
}
