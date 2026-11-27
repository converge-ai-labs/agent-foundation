# Connect existing environments

Use `list_connectable_environments` to find an authorized existing environment. Discovery does not start or connect it. Configured operations and client presence do not prove executable readiness; credentials and connection endpoints are Host-owned.

Call `connect_environment` with its `environment_id` and a new nonreserved `alias`. The tool targets your current Service Run; do not supply a Run or Thread ID. `workspace` and Host document mounts are reserved. The default mount and existing aliases do not change.

The default `persist_to_thread=false` adds the mount only to this Run. To save it for later ordinary Runs, first call `get_run` for the current `service_run_id`, then `get_thread` with the returned Thread ID. Pass `persist_to_thread=true` and the observed Thread `expected_version` as `id:version`. Both writes are atomic. After a version conflict reread and review the intended change; never silently refresh an approved version. Repeated same-target connections reuse the existing mount; a different target or policy under the same alias conflicts.

## Wait for the next model request

`accepted` means the mount was saved. The new environment enters your context and tool routing only at the next root model request, after this entire tool batch finishes. Do not issue Shell, Files or Computer calls using the new alias in the same batch, even sequentially inside an orchestration tool. Waiting or polling inside that batch cannot make it available earlier. No new user message is required: let the tool batch return and inspect the next model turn's current environment context.

Once the alias appears there, use the ordinary enabled tools, such as `shell_exec(command="pwd", alias="dev")`. First use may still prepare the environment and may fail if it is offline or unavailable. Connecting does not enable disabled tools or grant Computer access.

Read saved mounts with `get_run`, then `read_content` on its `content.environment_mounts` reference. This confirms accepted state, not live readiness. After an unknown write outcome reconcile the Run and, when persistence was requested, the Thread before retrying. Do not create a replacement environment or change an existing alias implicitly.
