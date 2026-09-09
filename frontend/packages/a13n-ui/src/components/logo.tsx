import type { ComponentProps } from "react";
import logo from "../brand/a13n-logo.svg";

export type LogoProps = Omit<
  ComponentProps<"img">,
  "src" | "srcSet" | "alt"
> & {
  alt: string;
};

export function Logo({ alt, width = 32, height = 32, ...props }: LogoProps) {
  return <img {...props} src={logo} alt={alt} width={width} height={height} />;
}
