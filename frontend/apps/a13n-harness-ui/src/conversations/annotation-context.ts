import { createContext } from "react";
import type { ReplyAnnotation } from "./reply-quote";

export const AnnotationContext = createContext<{
  notes: ReplyAnnotation[];
  active?: string;
  edit: (note: ReplyAnnotation, trigger: HTMLElement) => void;
  clear: () => void;
  remove: (id: string) => void;
} | null>(null);
