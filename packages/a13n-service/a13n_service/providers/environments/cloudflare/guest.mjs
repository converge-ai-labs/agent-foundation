// Root-owned supervisor. Agent commands run as UID 1000; the supervisor survives quiesce.
import fs from "node:fs";
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
const root = "/var/lib/a13n-cloudflare";
const marker = root + "/installed";
fs.mkdirSync(root, { recursive: true, mode: 0o700 });
if (!fs.existsSync(marker)) {
  execFileSync(
    "sh",
    [
      "-ec",
      "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates python3 git curl libgcc-s1 procps util-linux passwd && rm -rf /var/lib/apt/lists/*",
    ],
    { stdio: "ignore", timeout: 240000 },
  );
  const url =
    "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v0.5.1/a13n-envd-0.5.1-x86_64-unknown-linux-gnu.tar.gz";
  const response = await fetch(url, { signal: AbortSignal.timeout(120000) });
  if (!response.ok) throw new Error("Envd download failed");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (
    createHash("sha256").update(bytes).digest("hex") !==
    "f2fd6eace212c2e59d2b4d6a2dc69ec010235005ad332ac1ddd1932f7bca258c"
  )
    throw new Error("Envd checksum mismatch");
  fs.writeFileSync(root + "/envd.tar.gz", bytes, { mode: 0o600 });
  const binary = execFileSync(
    "tar",
    ["-xOzf", root + "/envd.tar.gz", "a13n-envd"],
    { maxBuffer: 128 * 1024 * 1024 },
  );
  fs.writeFileSync("/usr/local/bin/a13n-envd", binary, { mode: 0o755 });
  fs.unlinkSync(root + "/envd.tar.gz");
  execFileSync(
    "sh",
    [
      "-ec",
      "getent group 1000 >/dev/null || groupadd -g 1000 a13n; getent passwd 1000 >/dev/null || useradd -u 1000 -g 1000 -m -s /bin/bash a13n",
    ],
    { stdio: "ignore", timeout: 10000 },
  );
  fs.writeFileSync(marker, "0.5.1");
}
fs.mkdirSync("/workspace", { recursive: true });
fs.chownSync("/workspace", 1000, 1000);
fs.mkdirSync("/var/lib/a13n-envd", { recursive: true, mode: 0o700 });
fs.mkdirSync("/run/a13n-envd-state", { recursive: true, mode: 0o700 });
fs.writeFileSync(root + "/token", process.env.A13N_GUEST_TOKEN, {
  mode: 0o600,
});
delete process.env.A13N_GUEST_TOKEN;
for (const name of ["quiesce", "quiesced", "quiesce-failed"])
  fs.rmSync(root + "/" + name, { force: true });
const env = {
  ...process.env,
  A13N_ENVD_TRANSPORT: "http",
  A13N_ENVD_HTTP_BIND: "0.0.0.0:8888",
  A13N_ENVD_HTTP_PLAINTEXT_SCOPE: "provider_private_link",
  A13N_ENVD_HTTP_CREDENTIAL_FILE: root + "/token",
  A13N_ENVD_EXECUTION_UID: "1000",
  A13N_ENVD_EXECUTION_GID: "1000",
  A13N_ENVD_FULL_CONTROL: "true",
  A13N_ENVD_ALLOW_SUDO: "false",
  A13N_ENVD_STATE_DIR: "/var/lib/a13n-envd",
  A13N_ENVD_RUNTIME_DIR: "/run/a13n-envd-state",
};
const child = spawn(
  "/usr/bin/setpriv",
  ["--no-new-privs", "/usr/local/bin/a13n-envd"],
  { env, stdio: "ignore" },
);
let quiescing = false;
let stopping = false;
child.on("exit", () => {
  if (!stopping) process.exit(1);
});
setInterval(async () => {
  if (quiescing || !fs.existsSync(root + "/quiesce")) return;
  quiescing = true;
  stopping = true;
  try {
    fs.rmSync(root + "/quiesce", { force: true });
    fs.rmSync(root + "/quiesce-failed", { force: true });
    child.kill("SIGTERM");
    const deadline = Date.now() + 15000;
    while (
      child.exitCode === null &&
      child.signalCode === null &&
      Date.now() < deadline
    )
      await new Promise((r) => setTimeout(r, 100));
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL");
      const killed = Date.now() + 5000;
      while (
        child.exitCode === null &&
        child.signalCode === null &&
        Date.now() < killed
      )
        await new Promise((r) => setTimeout(r, 100));
      if (child.exitCode === null && child.signalCode === null)
        throw new Error("envd_stop_unconfirmed");
    }
    // No new execution can be admitted. Reap every process owned by the execution user.
    try {
      execFileSync("pkill", ["-KILL", "-u", "1000"], {
        stdio: "ignore",
        timeout: 5000,
      });
    } catch (error) {
      if (error.status !== 1) throw error;
    }
    await new Promise((r) => setTimeout(r, 200));
    const stat = fs
      .readdirSync("/proc")
      .filter((x) => /^\d+$/.test(x))
      .some((pid) => {
        try {
          return (
            fs.statSync("/proc/" + pid).uid === 1000 &&
            !fs.readFileSync("/proc/" + pid + "/stat", "utf8").includes(") Z ")
          );
        } catch {
          return false;
        }
      });
    if (stat) throw new Error("writers_remain");
    execFileSync("sync", [], { timeout: 10000 });
    fs.writeFileSync(root + "/quiesced", "ready", { mode: 0o600 });
  } catch {
    // Never exit PID 1 on suspend failure: that would discard the unsaved disk.
    quiescing = false;
    try {
      fs.writeFileSync(root + "/quiesce-failed", "failed", { mode: 0o600 });
    } catch {}
  }
}, 100);
