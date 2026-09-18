import { useSearchParams } from "react-router";

/** Parallel entry for redesigned surfaces while the current ones stay in place. */
export function useNextPreview() {
  const [params] = useSearchParams();
  return params.has("next");
}
