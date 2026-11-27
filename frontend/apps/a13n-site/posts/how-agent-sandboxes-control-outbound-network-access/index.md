---
title: How agent sandboxes control outbound network access
description: Follow an HTTPS upload through envd to see how a Linux network namespace, session DNS, and a Rust proxy enforce a sandbox’s domain allowlist.
date: '2026-11-27'
authors:
  - name: Yuxuan Chen
    url: https://github.com/YuxuanChen98
    avatar: https://avatars.githubusercontent.com/u/42828324?s=80
---

In the [previous post](/blog/how-an-execution-environment-runs-a-command/), envd started `python analyze.py` in an execution environment. Python read the sales records, added up the amounts by month, and saved the chart to `/workspace/chart.png`. It could do all of that offline.

Now the user asks the agent to upload the chart to the company’s reporting service. Suppose its public hostname is `reports.example.com`. When Python tries to connect over HTTPS, who decides whether the connection goes through?

## Why restrict a sandbox’s outbound network access?

The chart contains one total per month. The script that produced it can read every sale. A mistake in the upload code, or a third-party library that contacts another service, could send private records to a destination the user never approved.

Harness can check whether the agent has permission to run a command before it starts Python. The running process makes its own network connections, though. The command line `python analyze.py` tells us little about which services the script will contact. An **egress policy** lets the application specify which destinations processes in the environment can reach.

For this task, the application can grant access to the reporting service alone. That limits who can receive an upload. The script still has to select the right file.

## Which egress policies does envd support?

When the application opens an execution session, it chooses a network mode within the limits of the deployment. The choice applies to scripts and their child processes throughout that session.

| Mode         | Network access                                                   | When to use it                                                                     |
| ------------ | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `deny`       | The session has no route to external destinations.               | Generate the chart locally.                                                        |
| `controlled` | envd checks destinations against the session’s policy.           | Upload the chart to an approved service.                                           |
| `inherit`    | The session uses the network access its environment already has. | Keep existing deployment restrictions, such as an E2B sandbox’s `allow_out` rules. |

In `controlled` mode, there are two ways to specify destinations. Suppose both the reporting service and the documentation site have public hostnames:

- **`public`** allows the script to contact either site and other public services by name, without listing each one in advance. All the addresses returned for a hostname must be public.
- **`allowlist`** permits only the destinations listed. If the list contains just `reports.example.com`, the chart upload is allowed and a connection to the documentation site is denied. Hostnames match exactly, with no wildcards, and the same public-address check applies.

A script that connects directly to a numeric IP address needs an explicit allowlist entry for that public IP. For our upload, the reporting service’s hostname is enough.

## How does envd control a sandbox process’s connections?

The script uses a Python HTTP library. There are no calls to envd in the upload code. How does the policy apply?

On Linux deployments that support controlled egress, envd runs the session’s processes in a private **network namespace**. For this HTTPS upload, **nftables** rules in that namespace redirect the outbound TCP connection to a local listener. The envd broker, a Rust proxy on the same machine, accepts the connection and can open an upstream connection to an approved destination.

![Python and nftables run inside the session network namespace. The envd broker runs on the same sandbox host and connects to the external reporting service.](connection-path.svg "Figure 1. An HTTPS upload crosses the session’s network boundary on its way to the broker.")

The same rules apply if the script switches HTTP libraries or starts a child process to do the upload. But the proxy accepts a TCP connection addressed to an IP, and the allowlist names a domain. How does the proxy know which name the script requested?

## Why run a DNS server for the session?

The person configuring the upload knows the reporting service as `reports.example.com`. That name can stay the same when the service moves to a new IP. Conversely, two sites can share one public IP, so seeing the address alone tells the proxy little about which site the user approved.

envd supplies its own DNS service to controlled sessions. When Python looks up the reporting hostname, session DNS first checks whether the name is allowed. If it is, DNS allocates an address **inside the session**, records which hostname it represents, and returns the address to Python. A lookup for a name outside the allowlist is denied at this point.

![The DNS service checks reports.example.com and assigns it an address local to the session.](session-dns.svg "Figure 2. Session DNS keeps the hostname associated with the address it gives Python.")

Python then connects to the address it received. The broker can use the mapping to recover the requested hostname. It resolves the reporting service’s real public address when it is ready to open the upstream connection.

## How does the Rust proxy enforce the domain allowlist?

Follow the upload through three checks:

1. **Where was this TCP connection originally going?** When the broker accepts the redirected connection, it reads the original destination IP and port from the Linux socket option `SO_ORIGINAL_DST`. For this upload, the port is 443. The broker uses the IP to look up `reports.example.com` in the session DNS mapping, then checks the hostname against the current allowlist.
2. **Does the HTTP request name the same site?** After the TLS handshake with the script, the broker reads the HTTP `Host` header. It checks that header against the name recovered from the original destination IP. Here, `Host: reports.example.com` matches, as does `Host: reports.example.com:443`. A request naming a different site is rejected.
3. **Where does the hostname resolve now?** The broker resolves the hostname itself. If any answer contains a nonpublic address, it refuses the connection. Otherwise, it connects using a validated address, verifies the reporting service’s TLS certificate, and forwards the upload.

![The broker compares the hostname recovered from the TCP destination with the HTTP Host header before making an upstream connection.](broker-checks.svg "Figure 3. The session DNS mapping and the HTTP request provide two independent sources for the hostname.")

## Can the application revoke access during an upload?

Suppose the user cancels sharing while the upload is underway. The application can remove the reporting hostname from the session’s allowlist. envd will refuse new connections to that destination and close existing connections affected by the change. Python can keep working on the local report. Any bytes the reporting service has already received stay there.

The permission follows the task: the chart can be generated offline, the application can grant access to one site for the upload, and it can withdraw that access when sharing ends.
