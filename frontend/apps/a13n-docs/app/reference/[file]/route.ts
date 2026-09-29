import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { notFound } from "next/navigation";
import { referenceFiles } from "@/lib/site";

export const revalidate = false;

export async function GET(
  _request: Request,
  { params }: RouteContext<"/reference/[file]">,
) {
  const { file } = await params;
  const path = referenceFiles[file as keyof typeof referenceFiles];
  if (!path) notFound();

  return new Response(await readFile(join(process.cwd(), "../../..", path)), {
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export function generateStaticParams() {
  return Object.keys(referenceFiles).map((file) => ({ file }));
}
