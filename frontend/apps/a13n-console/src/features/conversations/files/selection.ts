import { createContext } from "react";
import type { FileReference } from "./assets";

export const PreviewFileContext = createContext<
  ((file: FileReference) => void) | null
>(null);
