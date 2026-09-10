Compose tool calls with inline Python in the restricted CodeAct sandbox.

Pass Python source in the code argument, without Markdown fences. The final expression is returned; print output is captured. Successful feeds retain temporary bindings only within this agent run. restart=true clears those bindings before evaluation; an unsuccessful feed clears them before returning. Neither operation deletes explicitly stored values.
