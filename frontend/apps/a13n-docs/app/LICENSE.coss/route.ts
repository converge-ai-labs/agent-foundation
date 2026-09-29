import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const revalidate = false;

/** Ships the Coss license with the a13n-ui theme compiled into the site's stylesheets. */
export async function GET() {
  return new Response(
    await readFile(join(process.cwd(), "node_modules/a13n-ui/LICENSE.coss")),
    {
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    },
  );
}
