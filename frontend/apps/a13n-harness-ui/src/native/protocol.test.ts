import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";
import { FileBuffer, joinPath } from "./buffer";
import { captureSource } from "./capture";
import { ThreadDraft, values } from "../conversations/draft";
import { submitDraft } from "../conversations/composer";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp("--native");
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {
    throw new Error("Unexpected authentication failure");
  });
  const status = await result(transport.client.GET("/api/status"));
  expect(status.features?.host_files).toBe(true);
  expect(status.features?.host_git).toBe(true);
}, 40000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);
const read = (path: string) =>
  result(
    transport.client.GET("/api/host/files/text", {
      params: { query: { path } },
    }),
  );

it("real App file revisions, raw transfers, paging, moves and deletion preserve native preconditions", async () => {
  const directory = app.native_root;
  const first = await result(
    transport.client.GET("/api/host/files", {
      params: { query: { path: directory, limit: 1 } },
    }),
  );
  expect(first.next_offset).toBe(1);
  const next = await result(
    transport.client.GET("/api/host/files", {
      params: {
        query: {
          path: directory,
          limit: 1,
          offset: 1,
          revision: first.directory.revision,
        },
      },
    }),
  );
  expect(next.entries[0].path).not.toBe(first.entries[0].path);
  const path = joinPath(directory, "sample.txt");
  const original = await read(path);
  const a = new FileBuffer(original),
    b = new FileBuffer(original);
  a.value = "saved by first reader";
  b.value = "private second reader";
  const saved = await result(
    transport.client.PUT("/api/host/files/text", {
      body: { path, expected_revision: a.base.entry.revision, text: a.value },
    }),
  );
  a.saved(saved, a.value);
  await expect(
    result(
      transport.client.PUT("/api/host/files/text", {
        body: { path, expected_revision: b.base.entry.revision, text: b.value },
      }),
    ),
  ).rejects.toMatchObject({ status: 409 });
  b.observe(await read(path));
  expect(b.value).toBe("private second reader");
  expect(b.conflict).toBe(true);
  await expect(
    captureSource(
      transport,
      (await result(transport.client.POST("/api/threads", { body: {} })))
        .thread_id,
      { file: original },
    ),
  ).rejects.toMatchObject({ status: 409 });
  expect((await read(joinPath(directory, "binary.bin"))).presentation).toBe(
    "binary",
  );
  expect((await read(joinPath(directory, "large.txt"))).presentation).toBe(
    "too_large",
  );
  const uploadPath = joinPath(directory, "uploaded.bin");
  await transport.fetch(
    `/api/host/files/content?${new URLSearchParams({ path: uploadPath })}`,
    { method: "PUT", body: new Uint8Array([0, 255, 4]) },
  );
  const uploaded = await read(uploadPath);
  const downloaded = await transport.fetch(
    `/api/host/files/content?${new URLSearchParams({ path: uploadPath, expected_revision: uploaded.entry.revision })}`,
  );
  expect([...new Uint8Array(await downloaded.arrayBuffer())]).toEqual([
    0, 255, 4,
  ]);
  const moved = await result(
    transport.client.POST("/api/host/files/move", {
      body: {
        path: uploadPath,
        destination: joinPath(directory, "moved.bin"),
        expected_revision: uploaded.entry.revision,
      },
    }),
  );
  const deleted = await result(
    transport.client.POST("/api/host/files/delete", {
      body: { path: moved.path, expected_revision: moved.revision },
    }),
  );
  expect(deleted.removed_entries).toBe(1);
});

it("precisely reviewed Files and Git captures replicate through the existing draft and submit immutable bytes", async () => {
  const threadId = (
    await result(
      transport.client.POST("/api/threads", {
        body: { title: "Native context" },
      }),
    )
  ).thread_id;
  const repository = joinPath(app.native_root, "repository");
  const git = await result(
    transport.client.GET("/api/host/git/status", {
      params: { query: { path: repository } },
    }),
  );
  expect(
    git.entries.find((entry) => entry.path === "tracked.txt"),
  ).toMatchObject({ index_status: "M", worktree_status: "M" });
  expect(git.entries.find((entry) => entry.path === "new.txt")?.kind).toBe(
    "untracked",
  );
  const staged = await result(
    transport.client.GET("/api/host/git/diff", {
      params: {
        query: {
          repository_path: repository,
          path: "tracked.txt",
          comparison: "staged",
        },
      },
    }),
  );
  const unstaged = await result(
    transport.client.GET("/api/host/git/diff", {
      params: {
        query: {
          repository_path: repository,
          path: "tracked.txt",
          comparison: "unstaged",
        },
      },
    }),
  );
  expect(staged.text).toContain("+staged");
  expect(unstaged.text).toContain("+worktree");
  const gitCapture = await captureSource(
    transport,
    threadId,
    { diff: unstaged },
    { start_line: 1, end_line: 3 },
  );
  expect(gitCapture.attachment.source).toMatchObject({
    kind: "git_diff",
    comparison: "unstaged",
    revision: unstaged.revision,
    start_line: 1,
    end_line: 3,
  });
  const sourcePath = joinPath(repository, "tracked.txt");
  const source = await read(sourcePath);
  const fileCapture = await captureSource(transport, threadId, {
    file: source,
  });
  await result(
    transport.client.PUT("/api/host/files/text", {
      body: {
        path: sourcePath,
        text: "changed after capture\n",
        expected_revision: source.entry.revision,
      },
    }),
  );
  await expect(
    captureSource(transport, threadId, { diff: unstaged }),
  ).rejects.toMatchObject({ status: 409 });
  const a = new ThreadDraft(),
    b = new ThreadDraft();
  const first = a.connect(transport, threadId, () => {}),
    second = b.connect(transport, threadId, () => {});
  try {
    await vi.waitFor(() => expect(a.synchronized && b.synchronized).toBe(true));
    a.doc.getText("text").insert(0, "Review these captured observations.");
    for (const capture of [fileCapture, gitCapture])
      a.doc
        .getMap("attachments")
        .set(
          capture.attachment.attachment_id,
          capture.attachment.attachment_id,
        );
    await vi.waitFor(() =>
      expect(
        a.synchronized &&
          b.synchronized &&
          values(b.doc).attachment_ids.length === 2,
      ).toBe(true),
    );
    await submitDraft(a, transport, threadId, "send");
    expect(a.submission.kind).toBe("accepted");
    for (const [capture, text] of [
      [fileCapture, "worktree\n"],
      [gitCapture, unstaged.text!.split("\n").slice(0, 3).join("\n") + "\n"],
    ] as const) {
      const response = await transport.fetch(
        `/api/threads/${threadId}/attachments/${capture.attachment.attachment_id}`,
      );
      expect(await response.text()).toBe(text);
    }
    await vi.waitFor(() => expect(values(b.doc).attachment_ids).toEqual([]));
  } finally {
    first.close();
    second.close();
    a.doc.destroy();
    b.doc.destroy();
  }
}, 20000);
