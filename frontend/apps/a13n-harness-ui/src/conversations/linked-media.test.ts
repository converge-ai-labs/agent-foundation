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

it("recognizes passive media candidates without treating active documents as media", () => {
  for (const path of [
    "/tmp/a.PNG",
    "C:\\media\\a.jpeg",
    "/tmp/a.webp",
    "/tmp/a.gif",
  ])
    expect(mediaKind(path)).toBe("image");
  for (const path of ["/tmp/a.mp3", "/tmp/a.wav", "/tmp/a.m4a", "/tmp/a.opus"])
    expect(mediaKind(path)).toBe("audio");
  for (const path of ["/tmp/a.mp4", "/tmp/a.webm", "/tmp/a.MOV"])
    expect(mediaKind(path)).toBe("video");
  for (const path of [
    "/tmp/a.svg",
    "/tmp/a.html",
    "/tmp/a.pdf",
    "/tmp/a.png.exe",
    "/tmp/a.png/child",
  ])
    expect(mediaKind(path)).toBeNull();
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

it("never requests remote, relative, active-document, or unrelated API links", () => {
  const tree = root(
    paragraph(
      ...[
        "https://external.test/a.png",
        "https://external.test" + link("/tmp/a.png"),
        "/api/host/files/content?path=/tmp/a.png",
        link("relative.png"),
        link("/tmp/a.svg"),
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
