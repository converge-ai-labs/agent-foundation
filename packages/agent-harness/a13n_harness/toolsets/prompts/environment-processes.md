<process-tool>
Values named `process-N` are opaque references valid only in the current logical run. Never invent, alter, or persist them.
Read bounded output incrementally and release completed processes when they are no longer needed.
Environment providers own process wall-time limits; the Harness adds no Agent-wide tool timeout.
</process-tool>
