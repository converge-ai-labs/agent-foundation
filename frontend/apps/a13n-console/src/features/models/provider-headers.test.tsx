import { expect, it } from "vitest";
import { serializeHeaders, type HeaderDraft } from "./provider-headers";

it("retains saved values while rotating and deleting headers", () => {
  const rows: HeaderDraft[] = [
    { id: "1", name: "X-Keep", value: "", savedName: "x-keep" },
    { id: "2", name: "X-Rotate", value: "replacement", savedName: "x-rotate" },
  ];
  expect(serializeHeaders(rows, ["x-keep", "x-rotate", "x-delete"])).toEqual({
    "x-rotate": "replacement",
    "x-delete": null,
  });
});
it("requires replacement material when a saved header is renamed", () => {
  expect(() =>
    serializeHeaders(
      [{ id: "1", name: "x-new", value: "", savedName: "x-old" }],
      ["x-old"],
    ),
  ).toThrow("Enter a value");
});
it("rejects case-insensitive duplicate headers", () => {
  expect(() =>
    serializeHeaders(
      [
        { id: "1", name: "X-Key", value: "one" },
        { id: "2", name: "x-key", value: "two" },
      ],
      [],
    ),
  ).toThrow("unique");
});
