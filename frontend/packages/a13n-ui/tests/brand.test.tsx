import { fireEvent, render } from "@testing-library/react";
import { expect, it } from "vitest";
import { BrandIcon, brands, resolveBrand } from "../src";

it("uses canonical identities, exact aliases and exact hosts without substring matches", () => {
  expect(resolveBrand({ identity: "github", alias: "notion" })).toBe(
    brands.github,
  );
  expect(resolveBrand({ alias: " Google_Calendar " })).toBe(
    brands.googlecalendar,
  );
  expect(resolveBrand({ endpoint: "https://mcp.notion.com/mcp" })).toBe(
    brands.notion,
  );
  expect(
    resolveBrand({
      alias: "my-github-provider",
      endpoint: "https://mcp.notion.com.evil.example/mcp",
    }),
  ).toBeUndefined();
});

it("includes the complete LobeHub catalog and its common name variants", () => {
  expect(Object.keys(brands).length).toBeGreaterThanOrEqual(322);
  expect(resolveBrand({ identity: " AdobeFirefly " })).toBe(
    brands.adobefirefly,
  );
  expect(resolveBrand({ alias: "adobe-firefly" })).toBe(brands.adobefirefly);
  expect(resolveBrand({ alias: "Firefly (Adobe)" })).toBe(brands.adobefirefly);
  expect(brands.adobefirefly.icon).toContain(
    "@lobehub/icons-static-svg@1.95.0/icons/adobefirefly-color.svg",
  );
});

it("falls back from a failed brand image to a safe provider logo then a generic icon", () => {
  const { container } = render(
    <BrandIcon identity="github" logo="https://logos.example/github.png" />,
  );
  const icon = container.querySelector("img")!;
  expect(icon.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(container.querySelectorAll("img")).toHaveLength(2);
  fireEvent.error(icon);
  expect(container.querySelector("img")?.src).toBe(
    "https://logos.example/github.png",
  );
  fireEvent.error(container.querySelector("img")!);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("svg")).not.toBeNull();
});

it("never loads an unsafe provider logo", () => {
  const { container } = render(
    <BrandIcon logo="https://user:secret@logos.example/icon.svg" />,
  );
  expect(container.querySelector("img")).toBeNull();
});
