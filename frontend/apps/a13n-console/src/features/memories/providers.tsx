import { KindProviders, type ProviderScope } from "../providers";

/** Accounts of the backends that keep record memories, such as mem0. */
export function MemoryProviders({ scope }: { scope: ProviderScope }) {
  return (
    <KindProviders
      kind="memory"
      scope={scope}
      addDescription="Choose the backend that keeps record memories."
      testDescription="Lists records of a namespace no memory uses, without changing anything."
    />
  );
}
