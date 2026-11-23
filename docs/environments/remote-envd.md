---
title: Remote Envd
description: Connect Harness, Harness UI, or Service to an external machine running a13n-envd over HTTP(S) or reverse WebSocket.
---

Use **Remote Envd** when an Agent should work on a machine you already operate (a build box, workstation, or VM) instead of a newly provisioned container.

## Connect to an existing HTTP daemon

1. Start `a13n-envd` on the target machine with `A13N_ENVD_TRANSPORT=http`, a stable `A13N_ENVD_DEVICE_ID`, and a protected credential file (`0600`). Use HTTPS outside loopback (`127.0.0.1`).
2. Configure TLS trust (`SSL_CERT_FILE` or a custom CA) on the Host; plain HTTP is accepted only on loopback or an explicitly trusted private link.
3. Wrap the connected client in a [`PreparedSource`](../a13n-harness/environments.md#supply-a-source) (Harness) or register it in Service.

## Connect to the Service

- **Outbound (recommended behind NAT/firewalls):** Run `a13n-envd connect-service` or pair from Console (**Environments → Instances → Connect my computer**). See [Connect a client computer](../a13n-service/environments.md#connect-a-client-computer).
- **Direct HTTP:** Register the HTTPS origin and token via `POST /api/v1/environments`. See [Register an external target](../a13n-service/environments.md#register-an-external-target).

## Connect to Harness UI

- Pair a Device via `a13n-envd connect https://your-webui-host.example.com` and approve it in WebUI ([Connect and approve Envd](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd)).
- Attach the Device to a Project or Thread ([Add Device bindings](../a13n-harness-ui/environments-and-projects.md#add-device-bindings)).
- Revoke trust anytime under **Settings → Environments → Devices** ([Revoke a Device](../a13n-harness-ui/environments-and-projects.md#revoke-a-device)).
- Enable shell commands on the daemon with `A13N_ENVD_FULL_CONTROL=1` or explicit shell profiles ([Enable commands](../a13n-envd/configuration.md#enable-commands)), and preserve [saved instances and Hosts](../a13n-envd/configuration.md#saved-instances-and-hosts) across restarts.

## Session egress and credential references

Configure `egress_mode: controlled` in Session configuration to restrict outbound destinations and inject HTTPS secrets without exposing raw tokens in environment variables. See [Session egress](../a13n-envd/egress.md).

## State, concurrency, and recovery

Hosts can store a Session recovery hint to reattach to a retained Session within the same daemon generation before its idle or max-lifetime expiry. Reattachment does not replay commands; if the daemon restarted or the Session expired, open a fresh Session.
