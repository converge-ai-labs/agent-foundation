import { expect, it } from "vitest";
import { ProcessObservations } from "./process-observations";

it("counts returned running shell handles, not foreground statuses or unrelated results", () => {
  const processes = new ProcessObservations();
  processes.status("run-one", "foreground", "running", null);
  processes.result(
    "run-one",
    "other_tool",
    {},
    { process_id: "other", status: { phase: "running" } },
  );
  expect(processes.background).toEqual([]);
  processes.result(
    "run-one",
    "shell_exec",
    JSON.stringify({ command: "pnpm   dev\n--host" }),
    JSON.stringify({ process_id: "process-one", status: { phase: "running" } }),
  );
  expect(processes.background).toEqual([
    {
      runId: "run-one",
      processId: "process-one",
      command: "pnpm dev --host",
      phase: "running",
      background: true,
    },
  ]);
});

it("does not resurrect completion when tool return is delivered after the callback", () => {
  const processes = new ProcessObservations();
  processes.status("run-one", "process-one", "exited", 7);
  processes.result(
    "run-one",
    "shell_exec",
    { command: "test" },
    { process_id: "process-one", status: { phase: "running" } },
  );
  expect(processes.background[0]).toMatchObject({
    phase: "exited",
    exitCode: 7,
    command: "test",
  });
  processes.result(
    "run-one",
    "shell_wait",
    {},
    { process_id: "process-one", status: { phase: "running" } },
  );
  expect(processes.background[0].phase).toBe("exited");
});

it("isolates reused handles by Run and marks only unfinished observations unavailable", () => {
  const processes = new ProcessObservations();
  for (const run of ["root", "child"])
    processes.result(
      run,
      "shell_start",
      { command: run },
      { process_id: "process-one", status: { phase: "running" } },
    );
  processes.end("root");
  expect(processes.background.map((item) => item.phase)).toEqual([
    "unavailable",
    "running",
  ]);
  processes.end();
  expect(
    processes.background.every((item) => item.phase === "unavailable"),
  ).toBe(true);
});

it("bounds retained observations and ignores malformed results", () => {
  const processes = new ProcessObservations();
  for (let index = 0; index < 130; index++)
    processes.result(
      "root",
      "shell_exec",
      {},
      { process_id: `process-${index}`, status: { phase: "running" } },
    );
  processes.result("root", "shell_exec", "bad", "not json");
  processes.result("root", "shell_exec", {}, { status: { phase: "running" } });
  expect(processes.background).toHaveLength(128);
  expect(processes.background[0].processId).toBe("process-2");
  expect(processes.omitted).toBe(true);
  processes.clear();
  expect(processes.background).toEqual([]);
  expect(processes.omitted).toBe(false);
});
