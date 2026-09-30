import { ProtocolError } from "../errors.js";

export interface SseFrame {
  id: string;
  event: string;
  data: string;
}
const maxFrameCharacters = 1024 * 1024;

/** Decode incremental UTF-8 and LF/CRLF/CR framing, including split delimiters. */
export async function* decodeSse(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "",
    id = "",
    event = "",
    values: string[] = [],
    size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      // An incomplete final UTF-8 sequence is part of an uncommitted frame, not a protocol violation.
      if (chunk.done) return;
      buffer += decoder.decode(chunk.value, { stream: true });
      let boundary: number;
      while ((boundary = buffer.search(/[\r\n]/)) >= 0) {
        if (
          buffer[boundary] === "\r" &&
          boundary === buffer.length - 1 &&
          !chunk.done
        )
          break;
        const line = buffer.slice(0, boundary);
        const width =
          buffer[boundary] === "\r" && buffer[boundary + 1] === "\n" ? 2 : 1;
        buffer = buffer.slice(boundary + width);
        size += line.length;
        if (size > maxFrameCharacters)
          throw new ProtocolError("SSE frame exceeds the size limit.");
        if (!line) {
          if (values.length)
            yield { id, event: event || "message", data: values.join("\n") };
          values = [];
          event = "";
          size = 0;
          continue;
        }
        if (line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator < 0 ? line : line.slice(0, separator);
        const value =
          separator < 0 ? "" : line.slice(separator + 1).replace(/^ /, "");
        if (field === "data") values.push(value);
        if (field === "event") event = value;
        if (field === "id" && !value.includes("\0")) id = value;
      }
      if (buffer.length + size > maxFrameCharacters)
        throw new ProtocolError("SSE frame exceeds the size limit.");
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
