# Compatibility Baseline Guard

These committed inputs are compatibility evidence, not generated snapshots of the current implementation. Follow the contribution policy in [CONTRIBUTING.md](/CONTRIBUTING.md#compatibility-baselines).

Before modifying, deleting, moving, or replacing an existing baseline, ask the human for explicit agreement. Explain the accepted input or behavior being changed and why preserving it in the implementation is insufficient. Do not update a fixture merely to make a failing test pass. Apply the same rule when weakening its semantic assertions or changing this guard.

New baselines may be added within an authorized compatibility task. Keep existing cases intact, use fictional data without credentials, and explain the added coverage. A request to fix compatibility does not by itself authorize removing old coverage.

PRs touching this directory must explicitly request human compatibility review. Automated tests and the PR notice do not constitute human approval.
