import type { Extension as FromMarkdownExtension } from "mdast-util-from-markdown";
import type { Code, State, Tokenizer } from "micromark-util-types";
import type { Processor } from "unified";
import type {} from "remark-parse";

// Recognize LaTeX delimiters before CommonMark's character escapes. Keeping
// this in the parser leaves code, link destinations and source offsets intact.
declare module "micromark-util-types" {
  interface TokenTypeMap {
    latexMath: "latexMath";
    latexMathData: "latexMathData";
    latexMathLineEnding: "latexMathLineEnding";
  }
}

const tokenize: Tokenizer = function (effects, ok, nok) {
  let closing: Code;
  return start;

  function start(code: Code): State | undefined {
    effects.enter("latexMath");
    effects.enter("latexMathData");
    effects.consume(code);
    return opening;
  }

  function opening(code: Code): State | undefined {
    if (code !== 40 && code !== 91) return nok(code);
    closing = code === 40 ? 41 : 93;
    effects.consume(code);
    return body;
  }

  function body(code: Code): State | undefined {
    if (code === null) return nok(code);
    if (code === -5 || code === -4 || code === -3) {
      effects.exit("latexMathData");
      effects.enter("latexMathLineEnding");
      effects.consume(code);
      effects.exit("latexMathLineEnding");
      effects.enter("latexMathData");
      return body;
    }
    effects.consume(code);
    return code === 92 ? escape : body;
  }

  function escape(code: Code): State | undefined {
    if (code === closing) {
      effects.consume(code);
      effects.exit("latexMathData");
      effects.exit("latexMath");
      return ok;
    }
    // A doubled backslash belongs to TeX (for example a matrix row break),
    // not to a closing delimiter starting at its second character.
    if (code === 92) {
      effects.consume(code);
      return body;
    }
    return body(code);
  }
};

const fromMarkdown: FromMarkdownExtension = {
  enter: {
    latexMath(token) {
      const source = this.sliceSerialize(token);
      const value = source.slice(2, -2).trim();
      this.enter(
        {
          type: "inlineMath",
          value,
          data: {
            hName: "code",
            hProperties: {
              className: [
                "language-math",
                source.startsWith("\\[") ? "math-display" : "math-inline",
              ],
            },
            hChildren: [{ type: "text", value }],
          },
        },
        token,
      );
    },
  },
  exit: {
    latexMath(token) {
      this.exit(token);
    },
  },
};

export function remarkLatex(this: Processor) {
  const data = this.data();
  (data.micromarkExtensions ??= []).push({
    text: { 92: { name: "latexMath", tokenize } },
  });
  (data.fromMarkdownExtensions ??= []).push(fromMarkdown);
}
