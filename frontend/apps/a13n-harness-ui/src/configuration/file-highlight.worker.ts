import { createHighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import { bundledLanguages } from "shiki/langs";
import { codeThemes } from "a13n-ui/code-theme";

export type TokenRange = [number, number, string, string];
self.onmessage = async ({
  data,
}: {
  data: { code: string; language: string; id: number };
}) => {
  const { code, language, id } = data;
  const load = Object.hasOwn(bundledLanguages, language)
    ? bundledLanguages[language as keyof typeof bundledLanguages]
    : undefined;
  if (!load) return;
  try {
    const grammar = await load();
    const highlighter = await createHighlighterCore({
      engine: createJavaScriptRegexEngine({ forgiving: true }),
      themes: [codeThemes.light, codeThemes.dark],
      langs: [grammar],
    });
    try {
      const lines = highlighter.codeToTokensWithThemes(code, {
        lang: language,
        themes: { light: codeThemes.light.name, dark: codeThemes.dark.name },
      });
      const tokens: TokenRange[] = [];
      for (const line of lines)
        for (const token of line) {
          const light = token.variants.light?.color,
            dark = token.variants.dark?.color;
          if (light && dark && token.content.length)
            tokens.push([
              token.offset,
              token.offset + token.content.length,
              light,
              dark,
            ]);
        }
      self.postMessage({ id, tokens });
    } finally {
      highlighter.dispose();
    }
  } catch {
    /* Keep the readable plain-text view when a grammar cannot tokenize a file. */
  }
};
