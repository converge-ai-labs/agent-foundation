import { docsLlms } from "@/lib/source";

export const revalidate = false;

export async function GET() {
  return new Response(await docsLlms.full("en"), {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}
