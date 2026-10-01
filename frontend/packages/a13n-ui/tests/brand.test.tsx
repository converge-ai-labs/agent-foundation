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
  expect(resolveBrand({ endpoint: "https://mcp.cloudflare.com/mcp" })).toBe(
    brands.cloudflare,
  );
  expect(resolveBrand({ endpoint: "https://app.airops.com/mcp" })).toBe(
    brands.airops,
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

it("uses the MCP icon when a remote endpoint has no known brand or usable logo", () => {
  const { container } = render(
    <BrandIcon
      endpoint="https://mcp.zoom.us/mcp/zoom/streamable"
      fallbackIdentity="mcp"
    />,
  );
  expect(container.querySelector("img")?.src).toBe(brands.mcp.icon);
});

it("prefers an explicit remote logo before the MCP fallback", () => {
  const { container } = render(
    <BrandIcon
      endpoint="https://search.parallel.ai/mcp"
      logo="https://logos.example/parallel.svg"
      fallbackIdentity="mcp"
    />,
  );
  const logo = container.querySelector("img")!;
  expect(logo.src).toBe("https://logos.example/parallel.svg");
  fireEvent.error(logo);
  expect(container.querySelector("img")?.src).toBe(brands.mcp.icon);
});

it.each([
  ["cerebras", "cerebras", "https://api.cerebras.ai/v1"],
  ["sambanova", "sambanova", "https://api.sambanova.ai/v1"],
  ["fireworks", "fireworks-ai", "https://api.fireworks.ai/inference/v1"],
  ["together", "togetherai", "https://api.together.xyz/v1"],
  ["together", "together.ai", "https://api.together.ai/v1"],
])(
  "resolves %s provider and catalog identities to SVG logos",
  (identity, alias, endpoint) => {
    const brand = brands[identity];
    expect(resolveBrand({ identity })).toBe(brand);
    expect(resolveBrand({ alias })).toBe(brand);
    expect(resolveBrand({ endpoint })).toBe(brand);
    expect(brand.icon).toBe(
      `https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.95.0/icons/${identity}-color.svg`,
    );
  },
);

it.each([
  ["vercel", "vercel-ai-gateway", "https://ai-gateway.vercel.sh/v1"],
  ["xai", "x-ai", "https://api.x.ai/v1"],
])(
  "resolves %s gateway identities and dark-mode SVGs",
  (identity, alias, endpoint) => {
    const brand = brands[identity];
    expect(resolveBrand({ alias })).toBe(brand);
    expect(resolveBrand({ endpoint })).toBe(brand);
    expect(brand.icon).toMatch(/\.svg$/);
    expect(brand.darkIcon || brand.invertInDark).toBeTruthy();
  },
);
