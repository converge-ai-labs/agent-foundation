import { expect, test } from "vitest";
import html from "../index.html?raw";

test("the heading and structured data name the site a13n", () => {
  // The hero draws its glyphs with CSS, so this is the heading's only text.
  expect(html).toMatch(/<h1[^>]*>\s*<span class="vh">a13n<\/span>/);
  const [, data] =
    html.match(/<script type="application\/ld\+json">([^<]+)<\/script>/) ?? [];
  const graph: { name: string }[] = JSON.parse(data ?? "")["@graph"];
  expect(graph.map((node) => node.name)).toEqual(["a13n", "a13n"]);
});
