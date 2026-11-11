---
title: Where agents run their commands
description: Execution environments, sandbox isolation, and the trade-offs between running commands alongside the agent or in a separate environment.
date: '2026-11-11'
authors:
  - name: Yuxuan Chen
    url: https://github.com/YuxuanChen98
    avatar: https://avatars.githubusercontent.com/u/42828324?s=80
---

A user asks an agent to analyze a CSV file. The agent inspects the file, installs the Python libraries it needs, runs an analysis script, and returns a chart.

It's a familiar workflow, but the application developer has a decision to make: where do those commands run? On the server running the agent, in a separate sandbox, or on the user's own computer?

That choice affects which data commands can access, how much damage a mistake can cause, and what happens to files and processes after the task ends.

## What is an execution environment?

The model requests tool calls to carry out a task. The agent runtime executes those calls and passes the results back to the model. Tools need compute and storage to do their work: CPUs run scripts and process data, while the filesystem holds input files, programs, and results. These resources let an agent carry out tasks such as analyzing data, changing code, and generating charts.

We call the target where an agent reads and writes files, runs programs, or interacts with a desktop an **execution environment**, or simply an **environment**. It can be a local machine, a Docker container, a cloud virtual machine, or a user's computer reached over a network.

For CSV analysis, the environment determines where the data lives, which versions of Python and its libraries are available, and where the chart is saved. For coding tasks, it also contains source code, build tools, and background services.

![An agent requests CSV analysis; the execution environment uses CPU resources to run Python and a filesystem to hold the input, script, and generated chart](environment-capabilities.svg "Figure 1. The environment supplies compute and storage for the task. The interface shown does not imply that execution must happen remotely.")

Once tasks need to install dependencies, start services, or reuse files in a later run, the application has more decisions to make: how to prepare the environment, which tasks may share it, and what to retain or clean up afterward.

## What should an environment restrict?

An execution environment gives an agent the resources to work with data and produce results. The next question is how much access to grant: which resources the agent may use, which operations it may perform, and what those operations may affect.

Trusted tasks on a personal computer need different restrictions from a shared server handling work for multiple users.

Security comes first, including the effect of mistakes on the host and other tasks. A deletion command with the wrong path can remove another project's files. Untrusted content can also steer an agent toward data outside its task. If commands run under the same operating-system account as the platform service, they may be able to access service configuration, credentials, or other users' files. The application therefore needs to specify which directories and network destinations are accessible, and which credentials the task actually needs.

Resource usage needs limits too. A program can keep spawning processes, exhaust memory, or fill the disk with logs. Without limits, one task can disrupt the agent runtime and other agents or services on the same machine.

Dependencies and lifecycles introduce further concerns. Two tasks may need different versions of a Python library, and a system configuration change for one task may break another. Separate environments make it easier to install dependencies independently, clean up after a task, or start again with a fresh environment.

A sandbox can enforce these restrictions. It limits the resources a program can access and the operations it can perform, reducing its effect on the host and other tasks. It might allow access only to a task directory, restrict network destinations, and cap memory and CPU usage. Containers, virtual machines, and operating-system isolation mechanisms can all be used to build sandboxes, with different isolation and security guarantees.

An execution environment describes where a program runs and which resources are available. A sandbox describes how those operations are isolated and restricted. An environment can use the local machine directly or run inside a sandbox.

The guarantees depend on the mechanisms and configuration. A container with excessive privileges or write access to important host directories can still affect the host. Using a sandbox requires an explicit decision about what it isolates and what it permits.

## Three deployment options

With the required capabilities and restrictions in mind, the next decision is how to deploy the agent runtime and its command execution environment. Three common arrangements are to execute commands directly on the agent's host, place each agent run and its commands together in a sandbox, or have the runtime connect to a separate environment. Each makes different trade-offs in permissions, resource management, lifecycles, and communication overhead.

![Three arrangements: two agents executing directly on one host, each agent and its commands in a separate sandbox on one host, and a runtime connected to a separate execution environment](deployment-options.svg "Figure 2. Three ways to deploy the agent runtime and its commands. A separate execution environment can be on the same machine or a different one; isolation depends on its configuration.")

### Execute commands directly on the agent's host

The simplest implementation lets the agent runtime start child processes on the machine where it runs.

This works well for local tools. Project files and development software are already on the user's computer, so commands can use them directly. There is no need to synchronize files or establish a remote connection. For trusted tasks and early prototypes, there is little setup involved.

The drawback is that commands may have access to resources the agent runtime itself depends on. If both run under the same account without additional restrictions, a command could modify the runtime's files or read credentials it should not have. Excessive resource usage can also prevent the runtime from continuing.

Whether direct execution is appropriate depends on how much the task is trusted, the account's permissions, and what else the machine is used for. Interactive work on a personal development machine calls for different restrictions from a service used by many people.

### Run each agent and its commands together in a sandbox

Another approach prepares a sandbox for each agent run and places both the runtime and the commands it starts inside it.

This gives the task a self-contained deployment. Programs, dependencies, and files are in one environment, and starting a command requires no network call. With isolation configured appropriately, changes made by one instance can be contained within it. The entire environment can be removed when the task finishes.

The outer sandbox primarily separates that instance from external systems. Inside it, commands that share permissions and resources with the agent runtime may still modify its files, read credentials available to it, or exhaust its memory. Protecting the runtime requires further separation of permissions and resources within the sandbox.

Their lifecycles are also closely tied. Stopping or rebuilding the sandbox stops the agent inside it. Retaining working files or resuming the agent requires storage and recovery arrangements. Each instance also needs the agent's runtime dependencies to be available and maintained.

For independent, short-lived tasks that can be deployed and removed as a unit, this is often a straightforward choice. Some applications also need to isolate the agent runtime itself, for example when it loads user-supplied extension code.

### Separate the agent runtime from its execution environment

A third approach has the runtime access a separate execution environment through an interface. They can occupy separate isolation boundaries on the same machine or run on different machines.

This allows each side to be managed independently. An analysis script runs in a restricted environment, while the agent runtime retains the capabilities it needs to call models, record task state, and handle user input. The execution environment receives only the access required for the task.

Files and environments can also outlive an individual agent run. After restarting, the runtime can reconnect to a target that still exists. A working directory can remain available for later tasks after the current one finishes.

Agents and environments need not have a one-to-one relationship. One agent can use multiple environments, such as running code in a cloud sandbox while reading authorized material from a user's computer. Multiple authorized agents can also use the same environment concurrently, sharing project files, dependencies, and services. One agent might analyze test logs while another examines the corresponding code, without each needing a complete copy of the environment. Concurrent changes still need coordination to prevent agents from overwriting each other's files.

These capabilities require additional engineering. The two sides need connections, authentication, file transfer, and handling for interrupted communication.

In particular, **a missing result does not mean a command never ran.** A command may have modified a file before the connection failed while returning its result. After reconnecting, the application needs to consult execution records or inspect the actual state before deciding what to do next. It cannot simply rerun every interrupted call.

Separation alone does not guarantee security or recovery. The environment needs appropriate access restrictions, and agent state needs to be saved. Whether processes survive a disconnection and whether their output can be read again are also design choices for the environment implementation.

The trade-offs can be summarized as follows:

| Arrangement                               | Main advantage                                                  | Main cost                                                                |
| ----------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Execute directly on the host              | Simple access to existing files and software                    | Commands may affect the host and other tasks                             |
| Put the agent and commands in one sandbox | A complete task environment deployed and removed as a unit      | Internal interference still needs attention; lifecycles are closely tied |
| Separate the runtime and environment      | Independent control over permissions, resources, and lifecycles | More communication, authentication, and failure handling                 |

The strength of isolation also depends on how the environment is configured, not just where components are deployed. The right arrangement depends on task trust, data location, retention needs, and the operational complexity the application can support.

## How a13n handles execution environments

In a13n, environments are resources that can be selected and managed independently. An application can use a local environment directly or connect to a container, a cloud sandbox, or an existing remote device such as a user's computer. Commands do not have to run remotely, and each agent run does not have to create a new sandbox.

We distinguish managing an environment from using it. Creating, starting, stopping, renewing, and destroying a target are resource-management responsibilities. Reading and writing files or executing commands on a selected target are execution capabilities.

This distinction makes lifecycles explicit. Closing an execution connection releases the connection and execution resources it owns without destroying the target itself.

![Agent run A disconnects while a retained environment and its files remain available; a later agent run B reconnects to reuse them](environment-lifecycle.svg "Figure 3. When retention is configured, the environment can outlive an individual agent run. Process survival and restoration of agent state require separate arrangements.")

An environment does not have to belong permanently to one agent instance. A task can use a dedicated environment, a later run can reuse an existing one, and authorized agents can share an environment for collaborative work.

Returning to the CSV example, a local application can read the file the user selects. A shared service can place it in a restricted sandbox. When data needs to remain on the user's device, the application can connect to that device and perform operations there, but it must also control what comes back: a remote agent can request file contents, and tool results may send those contents or analysis outputs to the cloud. Restrictions on returned data are a separate requirement.

Later posts will explore how environments are implemented in a13n.
