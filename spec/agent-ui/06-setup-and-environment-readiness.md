# Setup and Environment Readiness

## Design Position

Agent UI offers a guided first-use workflow before asking a new user to author configuration manually. Setup selects usable Models and Agents, a default Agent, a Project, and an Environment mode, then publishes ordinary human-editable resource files. It creates no private alternate configuration, copied credential store, daemon, or execution queue.

`AgentUiApp` owns credential-free discovery, starter composition, validation, and explicit publication. CLI, TUI, and WebUI present the same facts. TUI and WebUI offer setup when accepted Agent/Project defaults are missing; `a13n-ui setup` explicitly reopens the flow. Existing valid installations are not rewritten or forced through setup after an upgrade.

## Discovery and Choice

Discovery is read-only and bounded. It inspects accepted resources, default references, the launch directory, and the supported Codex/Grok account statuses under [Model Authentication](02a-model-authentication-and-account-stores.md). It does not print, return, copy, refresh, or exchange credentials and never starts login, a model request, or envd merely to populate choices.

An account projection distinguishes available, missing, unsupported, and invalid state with safe guidance. An expiring credential with a refresh grant can be offered for reuse; discovery does not promise a successful provider request. One provider's absent or malformed store must not prevent configuring the other provider or selecting an already configured Agent.

The landing workflow has three ordered steps with Back navigation that retains choices:

1. **Model connection**: choose API key (BYOK) or subscription (BYOS). Subscription discovery initially selects every available compatible provider. API-key configuration selects a model route and Host-local saved-key or environment-variable reference, never a key inside a resource file. Existing configured Agents remain reusable. **Not now** skips connection, clears new connection selections, and continues without authenticating or publishing files.
2. **Execution environment**: confirm the Project and its roots, then choose and check Sandbox or explicitly choose Full Control. The Sandbox check is cancellable, supports Retry, and is independent of model credentials. A checked selection is invalidated when Project roots or environment authority change. Continue requires a successful check for every effective Sandbox root, or the explicit Full Control choice.
3. **Agent**: offer a preselected default Agent, model-specific choices where applicable, and optional additional instructions. Review the credential-free files and defaults before explicitly finishing. The final confirmation preserves the preceding environment check rather than making the user discover it at submission time.

Skipping connection still permits complete publication of a default Agent and Project. An Agent without a Model is a valid unconfigured resource, not a runnable fallback: selecting it for execution fails with `agent_model_required` before any model or environment execution. Finishing setup does not claim authentication readiness or open the wizard repeatedly just because connection was deferred. A later setup can select a connected starter or existing Agent. Leaving setup entirely remains distinct from Not now and publishes no configuration; credentials already explicitly saved remain. Provider failures never silently fall back to another account.

The connection step offers explicit Key add/replace/delete and subscription authorization through [Model Authentication](02a-model-authentication-and-account-stores.md). CLI/TUI use device authorization by default; WebUI offers browser and device methods with the callback reachability distinction. No surface asks users to paste OAuth credentials. Successful authorization writes the compatible product store, then refreshes the available provider choice. A different shared account requires explicit replacement confirmation. Keys and subscription accounts save independently of configuration publication; Cancel setup does not undo an earlier explicit credential save.

## Reviewed Starter Configuration

Each supported subscription provider has one release-owned starter Model and Agent template. An API-key starter uses the explicitly entered supported route and credential reference without guessing credentials or model entitlement. With no new connection selected, setup offers an unconfigured default Agent. The templates use explicit authentication kinds and practical model-specific reasoning settings; they omit additional instructions unless the user supplies them. Every Agent receives the non-replaceable release-owned system prompt defined by [Agent composition](02-agent-composition-and-snapshots.md). Recommendations are based on official provider guidance and compatible upstream model catalogs rather than a runtime web search, benchmark slogan, or an automatically changing `latest` guess.

The starter Agent enables native Harness file/shell tools and project-aware skills. Additional task tools, context policies, and bounded child rosters can be authored through the ordinary composition catalog; setup does not invent new implementations for these features. It does not inject WebUI-only collaboration through YAML, enable arbitrary external MCP servers, or bypass tool approval policy. The built-in system prompt encourages inspecting relevant sources, preserving unrelated edits, concise progress, validating changes, and reporting incomplete work honestly. The setup screen identifies the selected model and allows users to choose their default; selecting both accounts creates both Agents rather than combining authentication or silently switching providers.

The reviewed September 2026 Codex starter is `openai-codex:gpt-5.6-terra` with `thinking: medium`. Setup also offers `gpt-5.6-sol` and `gpt-6-astra`; Astra is explicitly marked as entitlement/rollout-dependent, not universally available. The Grok starter is `grok:grok-4.6`. These choices follow the official [Codex models](https://developers.openai.com/codex/models) and [xAI models](https://docs.x.ai/developers/models) guidance and remain editable after creation.

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

Cancel before configuration publication writes no configuration; already saved credentials remain. Once publication starts, the operation returns or can be reconciled from the ordinary files; closing a dialog does not imply rollback. The UI preserves selections on a recoverable failure and refetches before retry. Browser publication temporarily blocks navigation and warns on unload. HTTP preflight disconnect cancels its owned probe, while cancelling browser delivery after publication cannot prove rollback. An unknown apply response requires inspecting ordinary files and generating another preview before an explicit retry. There is no separate permanent `setup_complete` flag that can hide broken defaults or trap a repaired installation in setup.

## Environment Readiness

The effective selected Environment, not the operating system alone, determines preparation. Native/Full Control performs no envd download, launch, Bubblewrap probe, namespace change, or administrator action. Custom and remote profiles retain their owning Provider contracts; local Linux isolation checks must not be incorrectly applied to them.

When Local Envd/Sandbox is selected at startup or through a selector, the App performs a bounded production-equivalent readiness check before first useful execution. It resolves the configured or release-owned executable, checks its version, and invokes the owning envd isolation probe using the effective isolation behavior. It does not use the success of a superficial `which bwrap` check as evidence that execution is possible.

Cancellation ends the pending request without a false readiness result. A completed result identifies ready or failed state and safe diagnostic facts: runtime availability/version, supported target, and actionable Bubblewrap, user-namespace or AppArmor failure where applicable. On Ubuntu, guidance explains that an administrator-installed per-Bubblewrap AppArmor profile is the recommended remedy for `kernel.apparmor_restrict_unprivileged_userns=1`, and links to [envd operations documentation](../../docs/agent-envd/index.md). The bounded error code and guidance distinguish unsupported platforms and actionable runtime failures without exposing raw probe stderr. Windows Sandbox is unavailable; explicit Full Control uses the host PowerShell profile instead of promising unsupported isolation. No diagnostic contains credential or raw unbounded subprocess output. Probe stdout and stderr are independently limited to 64 KiB; timeout and cancellation stop the trusted probe's owned POSIX session, including the separate process group created by the production isolation check, and drain remaining pipe bytes without retaining them. No platform-specific child cleanup is advertised as a Sandbox guarantee.

Failure offers explicit actions:

- view the copyable instructions and owning documentation;
- retry after fixing the prerequisite;
- cancel preparation while retaining the previous valid selection or blocked draft;
- explicitly choose Full Control, after explaining that it runs with the Host account and is not the Sandbox isolation boundary.

No retry, timeout, Escape, failed probe, or setup default silently downgrades Sandbox. The application never runs `sudo`, changes sysctls, installs system policy, or disables required isolation. An explicit Full Control choice follows the normal draft, exact-version Thread mutation, or confirmed setup-default publication boundary. A change during a Run affects only the next Run.

## Responsiveness and Scope

Startup paints before account discovery or runtime preparation. Preparation runs outside the UI intent lock and HTTP request/stream database sessions. Progress and cancellation remain usable; failures leave the shell mounted. Each result is correlated to the effective profile and selection request, so a late probe cannot reselect an old profile after the user switches or exits.

Readiness is an observation, not a permanent guarantee: executable or host policy can change after a successful probe. Actual execution retains its normal validation and failure handling. All effective Project roots must pass; an ignored new-project path must never substitute for an existing Project's roots. An App-lifetime result may avoid redundant probes for the same effective selection, while explicit Retry always checks again. No persisted ready marker authorizes another process or machine.

The WebUI server is the sole execution owner of its App. Browser navigation or disconnection does not create or kill a separate background process. Its setup APIs and model-visible Thread collaboration both call in-memory App services; neither uses IPC.

## Verification Invariants

1. Empty and partially configured installations can reach setup without a model call or copied credentials.
2. Codex-only, Grok-only, both-provider, neither-provider, invalid-store, and existing-Agent cases are explicit.
3. Both selected providers produce separate editable resources and one explicit default Agent.
4. Reopening setup preserves user edits; stale preconditions and occupied destinations fail without clobber.
5. Partial publication and prompt admission have independent observable completion boundaries.
6. Native mode never prepares envd; Sandbox failure never silently selects Native.
7. Successful probe, missing runtime, isolation denial, retry, cancellation, and explicit Full Control are covered by tests.
8. TUI and WebUI expose keyboard-accessible recovery with bounded safe diagnostics and preserved selections.
9. Initial recommendations are release-owned defaults, not a promise of universal model superiority or account entitlement.
