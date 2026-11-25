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
  for (const alias of ["mem0_oss", "mem0_platform"])
    expect(resolveBrand({ alias })).toBe(brands.mem0);
  expect(resolveBrand({ identity: "future-web-provider" })).toBeUndefined();
  expect(
    resolveBrand({
      alias: "my-github-provider",
      endpoint: "https://mcp.notion.com.evil.example/mcp",
    }),
  ).toBeUndefined();
});

it("falls back from a failed brand image to a safe provider logo then a generic icon", () => {
  const { container } = render(
    <BrandIcon identity="github" logo="https://logos.example/github.png" />,
  );
  const icon = container.querySelector("img")!;
  expect(icon.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(container.querySelectorAll("img")).toHaveLength(1);
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

it("keeps sign-in marks local and falls back when a bundled image fails", () => {
  for (const identity of [
    "google",
    "microsoft",
    "okta",
    "auth0",
    "github",
    "gitlab",
    "cloudflare",
    "authentik",
    "keycloak",
    "authelia",
    "zitadel",
  ]) {
    const brand = resolveBrand({ identity });
    expect(brand?.bundled, identity).toBe(true);
    const url = new URL(brand!.icon, location.href);
    expect(
      /^data:image\/svg\+xml[;,]/.test(url.href) ||
        (["http:", "https:"].includes(url.protocol) &&
          url.origin === location.origin),
      identity,
    ).toBe(true);
  }

  const { container, getByTestId } = render(
    <BrandIcon
      identity="authentik"
      bundledOnly
      fallback={<svg data-testid="shield" aria-hidden />}
    />,
  );
  const icons = container.querySelectorAll("img");
  expect(icons).toHaveLength(1);
  const [icon] = icons;
  expect(icon.getAttribute("src")).toBe(brands.authentik.icon);
  expect(icon.alt).toBe("");
  fireEvent.error(icon);
  expect(container.querySelector("img")).toBeNull();
  expect(getByTestId("shield")).toBeTruthy();
});

it.each(["generic", "notion"])(
  "never uses remote or caller-supplied images in bundled-only mode (%s)",
  (identity) => {
    const { container, getByTestId } = render(
      <BrandIcon
        identity={identity}
        bundledOnly
        logo="https://logos.example/icon.svg"
        fallbackIdentity="microsoft"
        fallback={<svg aria-hidden data-testid="shield" />}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(getByTestId("shield")).toBeTruthy();
  },
);
