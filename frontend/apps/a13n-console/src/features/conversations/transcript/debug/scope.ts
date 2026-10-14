import type { Schema } from "../../../../shared/api";

/** What every timeline row of one Run needs to know about that Run. */
export interface RunScope {
  run: Schema["RunView"];
  /** When the Run started, so each line can say how far in it happened. */
  start: number;
  /** Where an asynchronous delegation's child Thread can be opened, if known. */
  child: { path: string; runs: number } | null;
  /** The Run is waiting and this Console may answer it. */
  answerable: boolean;
  /** Move the reader to the dock, where a waiting Run is answered. */
  jumpToDock: () => void;
}
