import {
  DocumentationPage,
  pageMetadata,
  pageParams,
} from "@/components/documentation-page";
export default async function Page(props: PageProps<"/[...slug]">) {
  const { slug } = await props.params;
  return <DocumentationPage slug={slug} locale="en" />;
}
export const generateStaticParams = () => pageParams("en");
export async function generateMetadata(props: PageProps<"/[...slug]">) {
  return pageMetadata((await props.params).slug, "en");
}
