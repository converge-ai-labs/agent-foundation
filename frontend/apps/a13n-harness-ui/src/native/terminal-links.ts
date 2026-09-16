/** Only explicit absolute path:line references; never infer a shell's current directory. */
export function terminalFileLinks(text: string) {
  return [
    ...text.matchAll(
      /(?:^|[\s("'])((?:\/|[A-Za-z]:\\)[^\s:"')]+):([1-9]\d*)(?::([1-9]\d*))?/g,
    ),
  ]
    .filter((match) => Number.isSafeInteger(Number(match[2])))
    .map((match) => ({
      path: match[1]!,
      line: Number(match[2]),
      text: `${match[1]}:${match[2]}${match[3] ? `:${match[3]}` : ""}`,
      start: match.index! + match[0].indexOf(match[1]!),
    }));
}
