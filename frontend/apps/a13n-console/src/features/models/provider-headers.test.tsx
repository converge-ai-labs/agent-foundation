import { expect, it } from "vitest";
import {
  newHeaders,
  serializeHeaders,
  type HeaderDraft,
} from "./provider-headers";

const t = (key: string) => key;

it("retains saved values while rotating and deleting headers", () => {
  const rows: HeaderDraft[] = [
    { id: "1", name: "X-Keep", value: "", savedName: "x-keep" },
    { id: "2", name: "X-Rotate", value: "replacement", savedName: "x-rotate" },
  ];
  expect(serializeHeaders(rows, ["x-keep", "x-rotate", "x-delete"], t)).toEqual(
    {
      "x-rotate": "replacement",
      "x-delete": null,
    },
  );
});
it("requires replacement material when a saved header is renamed", () => {
  expect(() =>
    serializeHeaders(
      [{ id: "1", name: "x-new", value: "", savedName: "x-old" }],
      ["x-old"],
      t,
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
      t,
    ),
  ).toThrow("unique");
});
it("sends every header of a new provider with its value", () => {
  expect(
    newHeaders(
      [
        { id: "1", name: " X-Tenant ", value: "acme" },
        { id: "2", name: "x-region", value: "eu" },
      ],
      t,
    ),
  ).toEqual({ "x-tenant": "acme", "x-region": "eu" });
  expect(() =>
    newHeaders([{ id: "1", name: "x-tenant", value: "" }], t),
  ).toThrow("Enter a value");
});
