export const maxTextSize = 50_000;
export const maxEdges = 500;

export type FrameRequest = {
  kind: "render-mermaid";
  id: number;
  source: string;
  dark: boolean;
};
export type FrameReply =
  | { kind: "mermaid-ready" }
  | ({ kind: "mermaid-result"; id: number } & (
      | { svg: string; reason?: never }
      | {
          svg?: never;
          reason:
            | "unsupported-type"
            | "too-many-edges"
            | "invalid-diagram"
            | "image-unavailable";
        }
    ));
