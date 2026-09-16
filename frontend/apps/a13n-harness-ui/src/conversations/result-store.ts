export type FollowedThread = { threadId: string; acknowledged: number };

// IndexedDB serializes read/write transactions across tabs. A request's success
// is not a persistence boundary: publish acknowledgements only after commit.
export class ResultStore {
  constructor(private readonly name = "a13n-harness-ui.results") {}

  private async database(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(this.name, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore("threads", { keyPath: "threadId" });
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () =>
        reject(new Error("Result storage is blocked by another tab."));
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
    });
  }

  async read(): Promise<FollowedThread[]> {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("threads", "readonly");
      const request = transaction.objectStore("threads").getAll();
      transaction.oncomplete = () => {
        db.close();
        resolve(request.result as FollowedThread[]);
      };
      transaction.onabort = transaction.onerror = () => {
        db.close();
        reject(
          transaction.error ?? new Error("Could not read result storage."),
        );
      };
    });
  }

  async update(
    threadId: string,
    version: number,
    initialize: boolean,
  ): Promise<FollowedThread | undefined> {
    const db = await this.database();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction("threads", "readwrite");
      const store = transaction.objectStore("threads");
      const request = store.get(threadId);
      let value: FollowedThread | undefined;
      request.onsuccess = () => {
        const existing = request.result as FollowedThread | undefined;
        // A late tab's first snapshot must never acknowledge an existing result.
        value = existing
          ? {
              threadId,
              acknowledged: initialize
                ? existing.acknowledged
                : Math.max(existing.acknowledged, version),
            }
          : initialize
            ? { threadId, acknowledged: version }
            : undefined;
        if (value) store.put(value);
      };
      transaction.oncomplete = () => {
        db.close();
        resolve(value);
      };
      transaction.onabort = transaction.onerror = () => {
        db.close();
        reject(
          transaction.error ?? new Error("Could not save result storage."),
        );
      };
    });
  }
}
