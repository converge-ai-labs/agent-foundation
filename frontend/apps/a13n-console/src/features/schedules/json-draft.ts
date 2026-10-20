import { parse, printParseErrorCode, type ParseError } from "jsonc-parser";

const reasons: Record<ReturnType<typeof printParseErrorCode>, string> = {
  "<unknown ParseErrorCode>": "Invalid JSON syntax.",
  InvalidSymbol: "Unexpected JSON token.",
  InvalidNumberFormat: "Invalid number format.",
  PropertyNameExpected: "Expected a property name in double quotes.",
  ValueExpected: "Expected a JSON value.",
  ColonExpected: "Expected a colon after the property name.",
  CommaExpected: "Expected a comma between items.",
  CloseBraceExpected: "Expected a closing brace.",
  CloseBracketExpected: "Expected a closing bracket.",
  EndOfFileExpected: "Unexpected content after the JSON value.",
  InvalidCommentToken: "JSON does not allow comments.",
  UnexpectedEndOfComment: "JSON does not allow comments.",
  UnexpectedEndOfString: "Missing closing double quote.",
  UnexpectedEndOfNumber: "The number is incomplete.",
  InvalidUnicode: "Invalid Unicode escape sequence.",
  InvalidEscapeCharacter: "Invalid escape sequence.",
  InvalidCharacter: "Unexpected character in a string.",
};

export class JsonDraftSyntaxError extends SyntaxError {
  readonly line: number;
  readonly column: number;

  constructor(text: string, error?: ParseError) {
    super(
      error
        ? reasons[printParseErrorCode(error.error)]
        : "Invalid JSON syntax.",
    );
    const lines = text
      .slice(0, error?.offset ?? text.length)
      .split(/\r\n|\r|\n/);
    this.line = lines.length;
    this.column = lines.at(-1)!.length + 1;
  }
}

/** Native parsing remains authoritative; diagnostics use stable, translatable codes. */
export function parseJsonDraft(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    const errors: ParseError[] = [];
    parse(text, errors, { disallowComments: true, allowTrailingComma: false });
    throw new JsonDraftSyntaxError(text, errors[0]);
  }
}
