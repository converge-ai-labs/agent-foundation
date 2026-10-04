import { absoluteLinks } from "@/lib/site";
import { docsLlms } from "@/lib/source";

export const revalidate = false;

export async function GET() {
  return new Response(absoluteLinks(await docsLlms.index("en")), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}
