import { useRef, useState } from "react";

/** Suggestions follow selections until the user edits the name. */
export function useSuggestedName(initial?: string) {
  const [name, updateName] = useState(initial ?? "");
  const edited = useRef(initial !== undefined);
  return {
    name,
    setName(value: string) {
      edited.current = true;
      updateName(value);
    },
    suggestName(value: string) {
      if (!edited.current) updateName(value);
    },
  };
}
