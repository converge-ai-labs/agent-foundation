import { ChildProcess, spawn } from "node:child_process";
import { PassThrough } from "node:stream";
import { afterEach, expect, it, vi } from "vitest";
import { startApp } from "./app-fixture";

vi.mock("node:child_process", async (importOriginal) => ({
  ...(await importOriginal<typeof import("node:child_process")>()),
  spawn: vi.fn(),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function serverFixture() {
  const server = Object.assign(new ChildProcess(), {
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  vi.mocked(spawn).mockReturnValue(server);
  const exit = (code: number | null, signal: NodeJS.Signals | null = null) => {
    server.exitCode = code;
    server.signalCode = signal;
    server.emit("exit", code, signal);
  };
  const ready = () => server.stdout.write('{"origin":"http://fixture"}\n');
  return { server, exit, ready };
}

it("accepts graceful App shutdown", async () => {
  const { server, exit, ready } = serverFixture();
  const started = startApp();
  ready();
  const app = await started;
  server.stdin.on("finish", () => exit(0));
  await expect(app.close()).resolves.toBeUndefined();
});

it("reports an App that already exited abnormally with stderr", async () => {
  const { server, exit, ready } = serverFixture();
  const started = startApp();
  ready();
  const app = await started;
  server.stderr.write("cleanup failed");
  exit(1);
  await expect(app.close()).rejects.toThrow(
    "code=1, signal=null): cleanup failed",
  );
});

it("fails teardown when its shutdown watchdog must kill the App", async () => {
  vi.useFakeTimers();
  const { server, exit, ready } = serverFixture();
  const kill = vi.spyOn(server, "kill").mockImplementation(() => {
    exit(null, "SIGKILL");
    return true;
  });
  const started = startApp();
  ready();
  const app = await started;
  const closed = expect(app.close()).rejects.toThrow("signal=SIGKILL");
  await vi.advanceTimersByTimeAsync(15000);
  await closed;
  expect(kill).toHaveBeenCalledWith("SIGKILL");
});

it("preserves the startup failure when cleanup also fails", async () => {
  const { server, exit } = serverFixture();
  const started = startApp();
  server.stderr.write("startup failed");
  exit(2);
  const error = await started.catch((error: unknown) => error);
  expect(error).toBeInstanceOf(AggregateError);
  expect((error as AggregateError).errors.map(String)).toEqual([
    expect.stringContaining("Test App exited (2): startup failed"),
    expect.stringContaining(
      "Test App shutdown failed (code=2, signal=null): startup failed",
    ),
  ]);
});
