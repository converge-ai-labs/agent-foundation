export function connectCommand(
  origin: string,
  shell: "posix" | "powershell",
  execution: boolean,
  desktop: boolean,
) {
  // Quote even a configured origin: the command is pasted into a shell.
  const quoted = `'${origin.replaceAll("'", shell === "powershell" ? "''" : `'\\''`)}'`;
  const command = `a13n-envd connect ${quoted} --computer-use ${desktop}`;
  const grant = execution ? "1" : "0";
  // Override inherited grants on every launch; do not alter later terminal commands.
  return shell === "powershell"
    ? `& { $previous = $env:A13N_ENVD_FULL_CONTROL; try { $env:A13N_ENVD_FULL_CONTROL='${grant}'; ${command} } finally { $env:A13N_ENVD_FULL_CONTROL=$previous } }`
    : `A13N_ENVD_FULL_CONTROL=${grant} ${command}`;
}
