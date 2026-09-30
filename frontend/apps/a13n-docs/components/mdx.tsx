import defaultMdxComponents from "fumadocs-ui/mdx";
import type { ComponentProps } from "react";
import type { Locale } from "@/lib/i18n";
import type { MDXComponents } from "mdx/types";
import { Callout } from "./callout";
import { Mermaid } from "./mermaid";

export function getMDXComponents(
  components?: MDXComponents,
  locale: Locale = "en",
) {
  return {
    ...defaultMdxComponents,
    Callout: (props: ComponentProps<typeof Callout>) => (
      <Callout {...props} locale={locale} />
    ),
    Mermaid,
    ...components,
  } satisfies MDXComponents;
}

export const useMDXComponents = getMDXComponents;

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>;
}
