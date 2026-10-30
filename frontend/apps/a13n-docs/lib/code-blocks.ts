import { languageTitles } from "a13n-ui/code-theme";
import type { RehypeCodeOptions } from "fumadocs-core/mdx-plugins";

type ShikiTransformer = NonNullable<RehypeCodeOptions["transformers"]>[number];

/** Titles an untitled code block by its language, so every block names what it holds. */
export const transformerLanguageTitle: ShikiTransformer = {
  name: "a13n:language-title",
  pre(pre) {
    const title = languageTitles[this.options.lang];
    if (title && !pre.properties.title) pre.properties.title = title;
  },
};
