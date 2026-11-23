/** Open the posting conversation at an Inbox item's location. */
export const inboxTarget = (threadId: string, itemId: string) =>
  `${threadId === "thread_bot" ? "/bot" : `/threads/${encodeURIComponent(threadId)}`}?inbox=${encodeURIComponent(itemId)}`;
