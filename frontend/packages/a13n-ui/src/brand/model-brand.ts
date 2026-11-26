import { brands, resolveBrand } from "./brands";

const identities = Object.keys(brands).sort((a, b) => b.length - a.length);

/** Prefer the model family over the gateway hosting it. */
export function modelBrand(
  upstream: string,
  provider?: string,
): string | undefined {
  const parts = upstream
    .trim()
    .toLowerCase()
    .split(/[/:.]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .reverse();
  for (const part of parts) {
    if (resolveBrand({ identity: part, alias: part })) return part;
    const family = identities.find(
      (identity) =>
        part.startsWith(identity) &&
        /^([\s(\d._:-]|$)/.test(part.slice(identity.length)),
    );
    if (family) return family;
    if (/\b(claude|sonnet|opus|haiku)\b/.test(part)) return "claude";
    if (/\b(gemini|gemma)\b/.test(part)) return "gemini";
    if (/^glm[-\s\d]|\bglm\b/.test(part)) return "zhipu";
    if (/^(doubao[-.\s]|seed[-\s\d])/.test(part)) return "doubao";
    if (/^(gpt[-\s\d]|o[134](?:[-.\s(]|$))|\bcodex\b/.test(part))
      return "openai";
    if (/\bgrok\b/.test(part)) return "grok";
    if (/\bdeepseek\b/.test(part)) return "deepseek";
    if (/\bqwen\b/.test(part)) return "qwen";
    if (/\b(kimi|moonshot)\b/.test(part)) return "kimi";
    if (/\b(mistral|codestral|pixtral|ministral)\b/.test(part))
      return "mistral";
    if (/^llama(?:[-\s\d]|$)|\bllama\b/.test(part)) return "meta";
    if (/\bminimax\b/.test(part)) return "minimax";
  }
  return provider;
}
