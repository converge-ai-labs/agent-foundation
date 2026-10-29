# Frontend Specifications

The [repository model](../repository-model.md#frontend-workspace) owns workspace and release boundaries. Applications own product behavior; shared UI owns reusable primitives. Visual rules have one common foundation and explicit product owners.

| Contract                                                                  | Ownership                                                                                                    |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| [Shared design system](design-system.md)                                  | Common visual principles, semantic tokens, accessible primitives, themes and documentation-page presentation |
| [Console design](console-design-guidelines.md)                            | Console page anatomy, type, density, resource forms, navigation and execution presentation                   |
| [Console behavior](console.md)                                            | Hosted browser navigation, scoped settings, resource editing and conversation semantics                      |
| [Harness WebUI design](../a13n-harness-ui/webui/06-design-guidelines.md)  | Harness visual language, compact workbench, conversation details and overlays                                |
| [Harness workbench](../a13n-harness-ui/webui/04-workbench-interaction.md) | Harness journeys, placement, interaction and recovery                                                        |

Read the shared foundation and affected product guide for visual changes, and the owning behavior sections for interaction changes. Component READMEs document implementation usage rather than competing design rules.
