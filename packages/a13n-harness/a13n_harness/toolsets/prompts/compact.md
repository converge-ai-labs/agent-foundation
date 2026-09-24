Generate a compact continuation summary for the conversation history. Return only the summary text. Do not call tools. Do not carry a merely inspected or rejected candidate's workflow, mandatory requirements, referenced-resource instructions, or proposed next steps into any continuation section; if historically relevant, record only that it was inspected and not activated. Use this exact Markdown structure:

## Condensed conversation summary

### Analysis

[Brief analysis of the conversation and what matters for continuation.]

### Context

01. Primary Request and Intent: [User's explicit requests and intent]

02. Key Technical Concepts:

    - [Concepts, technologies, APIs, and architecture points]

03. Files and Code Sections:

    - [Files examined, edited, or created, with important details]

04. Problem Solving: [Problems solved and ongoing troubleshooting]

05. Pending Tasks:

    - [Explicit pending tasks]

06. Current Work: [Precise current work immediately before compaction. If the current user request references numbered items, "above", "that", or similar phrases, resolve those references using the previous assistant response and spell out what they refer to.]

07. Optional Next Step: [Direct next step aligned with the current work]

08. Past Interactions:

    - [Key interactions already completed, including actions and outcomes]

09. Skills for Continuation: [List only skills actually used that remain relevant to unfinished work. For each, include the SKILL.md path, its role in the current work, and supporting file paths needed for the immediate next step. Remind the resumed agent to re-read the relevant instructions before continuing work that depends on them. Do not include skills merely inspected, rejected, or no longer relevant.]

10. Files to Inspect on Resume: [List only file paths that may need to be inspected when resuming. Do not include file contents.]

11. Relevant Note Keys (omit this section when no supplied note key is relevant):

    - [Exact note key and why it matters for continuation]

Compact the conversation history into the requested continuation summary format. Focus on details needed to continue the user's work accurately after older messages are removed. Return only the summary text.

Do not call any tools or investigate unresolved questions yourself. Record any uncertainties in the summary for the resumed agent to investigate.
