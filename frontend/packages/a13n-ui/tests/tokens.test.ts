import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
const css = readFileSync("src/styles/tokens.css", "utf8");
function luminance(hex: string) {
  const channels = [1, 3, 5].map((offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722;
}
function contrast(foreground: string, background: string) {
  const a = luminance(foreground),
    b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
for (const theme of ["light", "dark"]) {
  it(`${theme} theme keeps normal text readable on shared surfaces`, () => {
    const block = css.split(`[data-a13n-theme="${theme}"]`)[1]!.split("}")[0]!;
    const colors = Object.fromEntries(
      [...block.matchAll(/--a13n-([\w-]+):\s*(#[0-9a-f]{6});/g)].map(
        (match) => [match[1], match[2]],
      ),
    );
    for (const foreground of ["text", "secondary", "muted"])
      for (const background of ["app", "canvas", "elevated", "selected"]) {
        expect(
          contrast(colors[foreground]!, colors[background]!),
          `${foreground} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    expect(
      contrast(colors["on-accent"]!, colors.accent!),
    ).toBeGreaterThanOrEqual(4.5);
  });
}
