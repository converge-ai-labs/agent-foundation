import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { once } from "node:events";

/** The returned path is synthetic native test data, never the repository cwd. */
export async function startNativeApp() {
  const server = spawn(
    "uv",
    [
      "run",
      "--locked",
      "--package",
      "a13n-harness-ui",
      "--no-default-groups",
      "python",
      "tests/protocol_server.py",
      "--native",
    ],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  let stderr = "";
  server.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const lines = createInterface({ input: server.stdout });
  const close = async () => {
    lines.close();
    if (server.exitCode === null && server.signalCode === null) {
      const exited = once(server, "exit");
      server.stdin.end("stop\n");
      const kill = setTimeout(() => server.kill("SIGKILL"), 15000);
      await exited;
      clearTimeout(kill);
    }
  };
  try {
    const address = await new Promise<{ origin: string; native_root: string }>(
      (resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error(`Native App startup timed out: ${stderr}`)),
          30000,
        );
        server.once("error", (error) => {
          clearTimeout(timer);
          reject(error);
        });
        server.once("exit", (code) => {
          clearTimeout(timer);
          reject(new Error(`Native App exited (${code}): ${stderr}`));
        });
        lines.on("line", (line) => {
          if (line.startsWith("{")) {
            clearTimeout(timer);
            resolve(JSON.parse(line));
          }
        });
      },
    );
    return { ...address, close };
  } catch (error) {
    await close();
    throw error;
  }
}
