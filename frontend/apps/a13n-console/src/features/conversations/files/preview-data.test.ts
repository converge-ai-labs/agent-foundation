import { expect, it } from "vitest";
import {
  csvPreview,
  formatJson,
  MEDIA_LIMIT,
  previewFormat,
  TEXT_LIMIT,
} from "./preview-data";

it.each([
  ["site.html", "image/png", "text", TEXT_LIMIT],
  ["diagram.svg", "application/octet-stream", "text", TEXT_LIMIT],
  ["diagram", "image/svg+xml", "text", TEXT_LIMIT],
  ["scan.pdf", "application/octet-stream", "pdf", MEDIA_LIMIT],
  ["scan.png", "image/png", "image", MEDIA_LIMIT],
  ["report.md", "text/plain; charset=utf-8", "markdown", TEXT_LIMIT],
  ["data.json", "application/problem+json", "json", TEXT_LIMIT],
  ["data.csv", "text/plain", "csv", TEXT_LIMIT],
  ["code.py", "application/octet-stream", "text", TEXT_LIMIT],
])(
  "chooses an inert or bounded viewer for %s (%s)",
  (name, content_type, kind, limit) => {
    expect(previewFormat({ name, content_type })).toMatchObject({
      kind,
      limit,
    });
  },
);

it("does not preview unknown binary formats", () => {
  expect(
    previewFormat({ name: "release.zip", content_type: "application/zip" }),
  ).toBeUndefined();
});

it("formats valid JSON without rounding numbers or removing duplicate keys", () => {
  expect(formatJson('{"id":9007199254740993,"id":2}')).toBe(
    '{\n  "id": 9007199254740993,\n  "id": 2\n}',
  );
  expect(formatJson("{broken")).toBe("{broken");
});

it("reads quoted commas, quotes, newlines, CRLF, and empty fields", () => {
  expect(
    csvPreview('Name,Note,Value\r\n"Doe, Jane","Line one\nLine ""two""",\r\n'),
  ).toEqual({
    rows: [
      ["Name", "Note", "Value"],
      ["Doe, Jane", 'Line one\nLine "two"', ""],
    ],
    truncated: false,
  });
});

it("bounds the grid by row and column count", () => {
  const row = Array.from({ length: 35 }, (_, index) => String(index)).join(",");
  const table = csvPreview(Array.from({ length: 120 }, () => row).join("\n"));
  expect(table.truncated).toBe(true);
  expect(table.rows).toHaveLength(101);
  expect(table.rows.every((cells) => cells.length === 30)).toBe(true);
});

it("keeps compact source for deeply nested JSON without growing indentation", () => {
  const input = "[".repeat(1000) + "0" + "]".repeat(1000);
  expect(formatJson(input)).toBe(input);
});
