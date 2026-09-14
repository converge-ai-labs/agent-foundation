import type { ComponentProps } from "react";
import logo from "./a13n-logo.svg";

export const a13nLogoUrl = logo;

export type LogoProps = Omit<
  ComponentProps<"img">,
  "src" | "srcSet" | "alt"
> & {
  alt: string;
};

export function Logo({ alt, width = 32, height = 32, ...props }: LogoProps) {
  return (
    <img {...props} src={a13nLogoUrl} alt={alt} width={width} height={height} />
  );
}
