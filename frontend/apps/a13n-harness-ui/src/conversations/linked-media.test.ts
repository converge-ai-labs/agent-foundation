// @vitest-environment node
import { expect, it } from "vitest";
import { linkedMediaPreviews } from "./linked-media";
import { mediaKind } from "../native/media-kind";

type Node = Parameters<ReturnType<typeof linkedMediaPreviews>>[0];
const currentHref = "https://harness.test/threads/current";
const link = (path: string) =>
  `/threads/current?native=files&native_path=${encodeURIComponent(path)}`;
const anchor = (href: string): Node => ({
  type: "element",
  tagName: "a",
  properties: { href },
  children: [{ type: "text", value: "Original" }],
});
const paragraph = (...children: Node[]): Node => ({
  type: "element",
  tagName: "p",
  children,
});
const root = (...children: Node[]): Node => ({ type: "root", children });
const transform = linkedMediaPreviews({ currentHref });

it("selects renderers by MIME family, not a filename or format allowlist", () => {
  for (const mime of [
    "image/png",
    "image/avif",
    "image/bmp",
    "image/new-format",
  ])
    expect(mediaKind(mime)).toBe("image");
  for (const mime of ["audio/ogg", "audio/x-wav", "audio/new-format"])
    expect(mediaKind(mime)).toBe("audio");
  for (const mime of ["video/mp4", "video/new-format"])
    expect(mediaKind(mime)).toBe("video");
  for (const mime of [
    undefined,
    "image/svg+xml",
    "text/html",
    "application/pdf",
    "application/octet-stream",
  ])
    expect(mediaKind(mime)).toBeNull();
});

it("deduplicates local media, keeps original anchors, and inserts controls outside paragraphs", () => {
  const original = anchor(link("/tmp/a.png"));
  const tree = root(
    paragraph(original),
    paragraph(anchor(link("/tmp/a.png")), anchor(link("/tmp/voice.wav"))),
  );
  transform(tree);
  expect(tree.children?.map((node) => node.tagName)).toEqual([
    "p",
    "figure",
    "p",
    "figure",
  ]);
  expect(tree.children?.[0].children?.[0]).toBe(original);
  expect(tree.children?.[1].properties?.dataHostMediaPath).toBe("/tmp/a.png");
  expect(tree.children?.[3].properties?.dataHostMediaPath).toBe(
    "/tmp/voice.wav",
  );
});

it("handles image Markdown and tight lists without nesting players inside a link", () => {
  const tree = root({
    type: "element",
    tagName: "ul",
    children: [
      {
        type: "element",
        tagName: "li",
        children: [
          {
            type: "element",
            tagName: "img",
            properties: { src: link("/tmp/a.png"), alt: "Portrait" },
          },
        ],
      },
    ],
  });
  transform(tree);
  const items = tree.children?.[0].children?.[0].children;
  expect(items?.map((node) => node.tagName)).toEqual(["a", "figure"]);
  expect(items?.[0].children?.[0].value).toBe("Portrait");
});

it.each(["https://external.test/full", link("/tmp/notes.txt")])(
  "preserves the outer destination %s when its thumbnail is Host media",
  (href) => {
    for (const wrappers of [[], ["strong"], ["strong", "em"]]) {
      const thumbnail: Node = {
        type: "element",
        tagName: "img",
        properties: { src: link("/tmp/a.png"), alt: "Thumbnail" },
      };
      const inline = wrappers.reduce<Node>(
        (child, tagName) => ({
          type: "element",
          tagName,
          children: [child],
        }),
        thumbnail,
      );
      const original = anchor(href);
      original.children = [inline];
      const tree = root(paragraph(original));
      transform(tree);
      expect(tree.children?.map((node) => node.tagName)).toEqual(
        href.startsWith("https://")
          ? ["p", "figure"]
          : ["p", "figure", "figure"],
      );
      expect(tree.children?.[0].children?.[0]).toBe(original);
      expect(original.properties?.href).toBe(href);
      expect(thumbnail).toEqual({
        type: "element",
        tagName: "span",
        properties: {},
        children: [{ type: "text", value: "Thumbnail" }],
      });
      expect(tree.children?.at(-1)?.properties?.dataHostMediaPath).toBe(
        "/tmp/a.png",
      );
    }
  },
);

it("retains standalone image navigation with the preview outside its paragraph", () => {
  const href = link("/tmp/a.png");
  const tree = root(
    paragraph({
      type: "element",
      tagName: "img",
      properties: { src: href, alt: "Thumbnail" },
    }),
  );
  transform(tree);
  expect(tree.children?.map((node) => node.tagName)).toEqual(["p", "figure"]);
  expect(tree.children?.[0].children?.[0]).toEqual({
    type: "element",
    tagName: "a",
    properties: { href },
    children: [{ type: "text", value: "Thumbnail" }],
  });
  expect(tree.children?.[1].properties?.dataHostMediaPath).toBe("/tmp/a.png");
});

it("never inspects remote, relative, or unrelated API links", () => {
  const tree = root(
    paragraph(
      ...[
        "https://external.test/a.png",
        "https://external.test" + link("/tmp/a.png"),
        "/api/host/files/content?path=/tmp/a.png",
        link("relative.png"),
        "javascript:alert(1)",
      ].map(anchor),
    ),
  );
  transform(tree);
  expect(tree.children).toHaveLength(1);
});

it("bounds previews while retaining every original link", () => {
  const tree = root(
    ...Array.from({ length: 10 }, (_, index) =>
      paragraph(anchor(link(`/tmp/${index}.png`))),
    ),
  );
  transform(tree);
  expect(
    tree.children?.filter((node) => node.tagName === "figure"),
  ).toHaveLength(8);
  expect(tree.children?.filter((node) => node.tagName === "p")).toHaveLength(
    10,
  );
});
