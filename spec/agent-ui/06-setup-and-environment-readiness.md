# Setup and Environment Readiness

## Design Position

Agent UI offers a guided first-use workflow before asking a new user to author configuration manually. Setup selects usable Models and Agents, a default Agent, a Project, and an Environment mode, then publishes ordinary human-editable resource files. It creates no private alternate configuration, copied credential store, daemon, or execution queue.

`AgentUiApp` owns credential-free discovery, starter composition, validation, and explicit publication. The inline workflow follows [Interactive CLI](07-interactive-cli.md). `a13n-ui setup` and `/setup` explicitly open it. Existing valid installations are not rewritten or forced through setup after an upgrade.

## Discovery and Choice

Discovery is read-only and bounded. It inspects accepted resources, default references, the launch directory, and the supported Codex/Grok account statuses under [Model Authentication](02a-model-authentication-and-account-stores.md). It does not print, return, copy, refresh, or exchange credentials and never starts login, a model request, or envd merely to populate choices.

An account projection distinguishes available, missing, unsupported, and invalid state with safe guidance. An expiring credential with a refresh grant can be offered for reuse; discovery does not promise a successful provider request. One provider's absent or malformed store must not prevent configuring the other provider or selecting an already configured Agent.

The inline workflow selects provider access, model-specific context and reasoning, execution permissions, optional subscription shell review, and a final publication preview. The invocation directory supplies the workspace; it is not a Project management step. Runtime readiness is deferred until execution needs it. Cancelling the inline selection leaves resource files untouched.

The underlying setup API can initialize unconfigured Agents or multiple providers for an embedding caller. An Agent without a Model is valid configuration but cannot execute. The CLI's guided flow selects one provider at a time and never treats missing credentials as a reason to substitute another provider.

Subscription authorization and hidden API-key storage are independent explicit operations under [Model Authentication](02a-model-authentication-and-account-stores.md). Inline login uses device authorization; the auth CLI also supports browser authorization with the callback reachability distinction. Setup accepts a stored-key reference or environment-variable name, not raw secret input. Cancelling setup does not undo a completed credential operation.

## Reviewed Starter Configuration

Each supported subscription provider has one release-owned starter Model and Agent template. An API-key starter uses the explicitly entered supported route and credential reference without guessing credentials or model entitlement. With no new connection selected, setup offers an unconfigured default Agent. The templates use explicit authentication kinds and practical model-specific reasoning settings; they omit additional instructions unless the user supplies them. Every Agent receives the non-replaceable release-owned system prompt defined by [Agent composition](02-agent-composition-and-snapshots.md). Recommendations are based on official provider guidance and compatible upstream model catalogs rather than a runtime web search, benchmark slogan, or an automatically changing `latest` guess.

The starter Agent enables native Harness file/shell tools and project-aware skills. Native runtime-context, handoff, and compaction capabilities are also selected. Additional task tools and bounded child rosters can be authored through the ordinary composition catalog; setup does not invent new implementations for these features. It does not inject host-only collaboration through YAML, enable arbitrary external MCP servers, or bypass tool approval policy. The built-in system prompt encourages inspecting relevant sources, preserving unrelated edits, concise progress, validating changes, and reporting incomplete work honestly. The setup screen identifies the selected model and allows users to choose their default; selecting both accounts creates both Agents rather than combining authentication or silently switching providers.

Codex defaults and explicit context choices are defined by [Interactive CLI](07-interactive-cli.md#setup-and-context-management). Grok defaults to `grok:grok-4.6`. These are reviewed, editable recommendations, not entitlement checks.

Subscription setup offers enabled shell review by default. When Codex is selected, a separate `openai-codex:gpt-5.6-luna` resource with `thinking: low` supplies the reviewer, including for a Grok starter selected alongside Codex. Grok-only setup reuses Grok 4.6 for review and explicitly does not claim that it is a verified cheaper subscription tier. Both flagged commands and review failures require approval. Shell review is not filesystem/process isolation, and disabling it is an explicit setup choice. Auxiliary-model recipes follow the frozen composition contract.

Configuration is written into the selected tree: root YAML defaults and ordinary `models/`, `agents/`, and, when explicitly selected, `projects/` resources. Authentication fields reference the existing compatible store kind and contain no token bytes. The default Environment remains Full Control unless the user explicitly selects Sandbox; model recommendations do not change that omission default.

Templates seed files once. An explicit `connect_default` selection binds the selected existing Agent to the selected connection Model and optionally replaces its additional instructions when nonempty instructions are supplied. Its other fields are preserved. A changed API-key route/authentication gets a distinct Model resource rather than changing a Model shared with other Agents. An edited Model occupying a generated ID is checked against the requested route and authentication before reuse; a mismatch conflicts instead of trusting the ID. Unselected resources remain unchanged. Additional instructions never replace the built-in system prompt. Later releases, setup discovery, or ordinary startup never replace user-edited instructions, model routes, settings, tools, or resource names. Every new Run capture includes the current release system prompt alongside any non-empty additional instructions; frozen Runs retain the exact text already captured. Users can edit these files directly. The setup instruction editor targets new Agents; choosing an existing Agent preserves its instructions and does not submit stale text from another selection. The selected default affects new Threads only.

## Publication and Recovery

Setup receives an explicit selection and current source-generation precondition. It validates all chosen resource IDs, supported templates, default Agent membership, absolute Project roots, and Environment selection before source publication. No requested path can escape the fixed configuration tree. Existing resource IDs and destinations are reused only when they are the intended existing resource; a different existing file is a conflict, not a clobber opportunity.

Publication follows the shared source boundary:

- prepare and validate the complete candidate before writing;
- create new resources with no-clobber operations;
- update the explicitly selected Agent and root defaults only against observed exact source digests, preserving unrelated fields;
- publish defaults after the resources they reference;
- accept the final coherent generation and return the actual source paths and defaults.

A filesystem tree is not a multi-file transaction. Interruption may leave valid newly created resource files without selecting new defaults. Such partial results are reported or found by the next discovery; retry reuses matching files instead of duplicating resources. Setup never rolls back by deleting a file it cannot prove it still owns, never removes unrelated user files, and never treats partial publication as a successful completed setup. A concurrent atomic external save conflicts and preserves the competing content. For an existing root or explicitly selected Agent, setup detaches the observed file into a private same-filesystem recovery directory, checks the detached digest, and publishes the new root with no-clobber creation. This deliberately favors recoverability over an unconditional replace: there can be a brief missing-root interval, and the App retains its previously accepted generation. Failure restores the detached file only when the root remains absent. If another writer has occupied the root, both versions survive and the conflict identifies `.a13n-ui-setup-recovery-*/<root-name>`. The recovery directory and parent directory are synced after detaching a root; restored/replacement root directory entries are synced before deleting the backup, and backup-directory removal is also synced. A crash leaving that recovery file blocks further setup publication until the user reviews and restores or moves it. No general transaction or protection against arbitrary writes through already-open file descriptors is claimed.

Cancel before configuration publication writes no resource selections; already saved credentials remain. Once publication starts, ordinary files are the reconciliation surface and cancellation does not imply rollback. An unknown apply result requires inspecting files and generating another preview before explicit retry. There is no permanent `setup_complete` flag that hides broken defaults.

## Environment Readiness

The effective selected Environment, not the operating system alone, determines preparation. Native/Full Control performs no envd download, launch, Bubblewrap probe, namespace change, or administrator action. Custom and remote profiles retain their owning Provider contracts; local Linux isolation checks must not be incorrectly applied to them.

When Local Envd/Sandbox is selected at startup or through a selector, the App performs a bounded production-equivalent readiness check before first useful execution. It resolves the configured or release-owned executable, checks its version, and invokes the owning envd isolation probe using the effective isolation behavior. It does not use the success of a superficial `which bwrap` check as evidence that execution is possible.

Cancellation ends the pending request without a false readiness result. A completed result identifies ready or failed state and safe diagnostic facts: runtime availability/version, supported target, and actionable Bubblewrap, user-namespace or AppArmor failure where applicable. On Ubuntu, guidance explains that an administrator-installed per-Bubblewrap AppArmor profile is the recommended remedy for `kernel.apparmor_restrict_unprivileged_userns=1`, and links to [envd operations documentation](../../docs/agent-envd/index.md). The bounded error code and guidance distinguish unsupported platforms and actionable runtime failures without exposing raw probe stderr. Windows Sandbox is unavailable. Explicit Full Control provides native file access but does not promise Windows command execution: the current Direct Local process backend requires POSIX. No diagnostic contains credential or raw unbounded subprocess output. Probe stdout and stderr are independently limited to 64 KiB; timeout and cancellation stop the trusted probe's owned POSIX session, including the separate process group created by the production isolation check, and drain remaining pipe bytes without retaining them. No platform-specific child cleanup is advertised as a Sandbox guarantee.

Failure offers explicit actions:

- view the copyable instructions and owning documentation;
- retry after fixing the prerequisite;
- cancel preparation while retaining the previous valid selection or blocked draft;
- explicitly choose Full Control, after explaining that it runs with the Host account and is not the Sandbox isolation boundary.

No retry, timeout, Escape, failed probe, or setup default silently downgrades Sandbox. The application never runs `sudo`, changes sysctls, installs system policy, or disables required isolation. An explicit Full Control choice follows the normal draft, exact-version Thread mutation, or confirmed setup-default publication boundary. A change during a Run affects only the next Run.

## Responsiveness and Scope

Startup paints before account discovery or runtime preparation. Preparation runs outside terminal rendering and database transactions. Progress and cancellation remain usable; failures leave the shell mounted. Each result is correlated to the effective profile and selection request, so a late probe cannot reselect an old profile after the user switches or exits.

Readiness is an observation, not a permanent guarantee: executable or host policy can change after a successful probe. Actual execution retains its normal validation and failure handling. All effective Project roots must pass; an ignored new-project path must never substitute for an existing Project's roots. An App-lifetime result may avoid redundant probes for the same effective selection, while explicit Retry always checks again. No persisted ready marker authorizes another process or machine.

The CLI owns the App lifetime in one task. Terminal exit cancels work and waits for App cleanup; it does not detach a service.

## Verification Invariants

1. Empty and partially configured installations can reach setup without a model call or copied credentials.
2. Codex-only, Grok-only, both-provider, neither-provider, invalid-store, and existing-Agent cases are explicit.
3. Both selected providers produce separate editable resources and one explicit default Agent.
4. Reopening setup preserves user edits; stale preconditions and occupied destinations fail without clobber.
5. Partial publication and prompt admission have independent observable completion boundaries.
6. Native mode never prepares envd; Sandbox failure never silently selects Native.
7. Successful probe, missing runtime, isolation denial, retry, cancellation, and explicit Full Control are covered by tests.
8. The CLI exposes command-based recovery with bounded safe diagnostics and preserved selections.
9. Initial recommendations are release-owned defaults, not a promise of universal model superiority or account entitlement.
