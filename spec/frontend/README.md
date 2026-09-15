# Frontend Specifications

The [repository model](../repository-model.md#frontend-workspace) owns workspace and release boundaries. Applications own product behavior; the shared UI package owns reusable visual primitives.

| Contract                          | Ownership                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| [Design system](design-system.md) | Shared tokens, component behavior, themes, localization boundary, and development showcase |

[Console](console.md) owns hosted browser navigation, scoped settings, resource editing and conversation interaction. [Bots Integration](bots.md) owns customer-owned Slack/Feishu onboarding and Bot management. [Bot Memory](bot-memory.md) owns scoped record browsing and sharing, with embedded product prototypes.
