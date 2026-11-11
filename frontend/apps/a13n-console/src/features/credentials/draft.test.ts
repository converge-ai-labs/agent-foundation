import { expect, it } from "vitest";
import {
  metadataPatch,
  parseHosts,
  rebaseMetadata,
  validHosts,
  validToken,
} from "./draft";

it("normalizes exact hosts and rejects URLs, IPs, patterns and malformed DNS labels", () => {
  expect(parseHosts("API.GITHUB.COM., api.github.com\nexample.com")).toEqual([
    "api.github.com",
    "example.com",
  ]);
  for (const host of [
    "https://api.github.com",
    "*.github.com",
    "127.0.0.1",
    "::1",
    "a..com",
    "-a.com",
    "a_.com",
    "域名.com",
    "a".repeat(64) + ".com",
  ]) {
    expect(validHosts([host]), host).toBe(false);
  }
  expect(validHosts([])).toBe(false);
  expect(
    validHosts(Array.from({ length: 257 }, (_, i) => `host${i}.test`)),
  ).toBe(false);
  expect(validHosts(["api.github.com", "xn--bcher-kva.example"])).toBe(true);
});

it("counts UTF-8 token bytes, preserves spaces and refuses control characters", () => {
  expect(validToken("token with spaces")).toBe(true);
  expect(validToken("😀".repeat(2048))).toBe(true);
  expect(validToken("😀".repeat(2048) + "x")).toBe(false);
  for (const value of ["", "x\ny", "x\ry", "x\ty", "x\u0000y", "x\u007fy"])
    expect(validToken(value)).toBe(false);
});

it("rebases only edited metadata without inventing token or allowed-domain edits", () => {
  const basis = { name: "Original", description: "Old description" };
  const latest = {
    ...basis,
    name: "Remote name",
    description: "New description",
  };
  const draft = { name: " My name ", description: basis.description };
  expect(metadataPatch(draft, basis)).toEqual({ name: "My name" });
  expect(rebaseMetadata(draft, basis, latest)).toEqual({
    name: "My name",
    description: "New description",
  });
});
