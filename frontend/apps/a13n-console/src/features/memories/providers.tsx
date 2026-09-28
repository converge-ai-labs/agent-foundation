import { KindProviders } from "../providers";

/** Accounts of the backends that keep record memories, such as mem0. */
export function MemoryProviders() {
  return (
    <KindProviders
      kind="memory"
      addDescription="Choose the backend that keeps record memories."
      testDescription="Lists records of a namespace no memory uses, without changing anything."
    />
  );
}
