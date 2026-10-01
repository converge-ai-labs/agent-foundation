import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { open, writeFile } from "node:fs/promises";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";
import { FileBuffer, joinPath } from "./buffer";
import { captureSource } from "./capture";
import { fileTransfer } from "./file-transfer";
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

it("streams large reviewed media with native ranges while keeping file access scoped and captures bounded", async () => {
  const path = joinPath(app.native_root, "streamed.mp4");
  const size = 15_047_567;
  const file = await open(path, "w");
  try {
    await file.truncate(size);
    await file.write(Buffer.from("HEAD"), 0, 4, 0);
    await file.write(Buffer.from("TAIL"), 0, 4, size - 4);
  } finally {
    await file.close();
  }
  const reviewed = await result(
    transport.client.GET("/api/host/files/info", {
      params: { query: { path } },
    }),
  );
  expect(reviewed.media_type).toBe("video/mp4");
  expect("text" in reviewed).toBe(false);
  const access = await fileTransfer(
    transport,
    path,
    reviewed.entry.revision,
    "inline",
  );
  const url = new URL(access.url, app.origin);
  expect(url.searchParams.has("key")).toBe(false);
  expect(
    (
      await fetch(new URL("/api/host/files/transfers", app.origin), {
        method: "POST",
        body: "{}",
      })
    ).status,
  ).toBe(401);
  expect(
    (await fetch(new URL("/api/host/files/transfer", app.origin))).status,
  ).toBe(401);
  const first = await fetch(url, { headers: { Range: "bytes=0-3" } });
  expect(first.status).toBe(206);
  expect(first.headers.get("content-range")).toBe(`bytes 0-3/${size}`);
  expect(first.headers.get("content-type")).toBe("video/mp4");
  expect(first.headers.get("content-disposition")).toMatch(/^inline;/);
  expect(first.headers.get("cache-control")).toBe("no-store");
  expect(await first.text()).toBe("HEAD");
  const tail = await fetch(url, { headers: { Range: "bytes=-4" } });
  expect(tail.status).toBe(206);
  expect(await tail.text()).toBe("TAIL");
  const seek = await fetch(url, { headers: { Range: `bytes=${size - 4}-` } });
  expect(seek.status).toBe(206);
  expect(await seek.text()).toBe("TAIL");
  const head = await fetch(url, {
    method: "HEAD",
    headers: { Range: "bytes=0-3" },
  });
  expect(head.status).toBe(200);
  expect(head.headers.get("content-length")).toBe(String(size));
  expect(head.headers.get("etag")).toBe(`"${reviewed.entry.revision}"`);
  expect(await head.text()).toBe("");
  const unsatisfiable = await fetch(url, {
    headers: { Range: `bytes=${size}-` },
  });
  expect(unsatisfiable.status).toBe(416);
  expect(unsatisfiable.headers.get("content-range")).toBe(`bytes */${size}`);
  const conditional = await fetch(url, {
    headers: { Range: "bytes=0-3", "If-Range": '"other-revision"' },
  });
  expect(conditional.status).toBe(200);
  expect(conditional.headers.get("content-length")).toBe(String(size));
  await conditional.body?.cancel();
  expect(
    (await fetch(url, { headers: { Origin: "https://untrusted.invalid" } }))
      .status,
  ).toBe(403);
  expect((await fetch(url, { method: "PUT" })).status).toBe(401);
  const token = url.searchParams.get("token")!;
  expect(
    (
      await fetch(
        new URL(
          `/api/host/files?path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`,
          app.origin,
        ),
      )
    ).status,
  ).toBe(401);
  const tampered = new URL(url);
  tampered.searchParams.set("token", token + "x");
  expect((await fetch(tampered)).status).toBe(401);
  const download = await fileTransfer(
    transport,
    path,
    reviewed.entry.revision,
    "attachment",
  );
  const bytes = await fetch(new URL(download.url, app.origin));
  expect(bytes.headers.get("content-type")).toBe("application/octet-stream");
  expect(bytes.headers.get("content-disposition")).toMatch(/^attachment;/);
  let received = 0;
  const reader = bytes.body!.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.length;
    }
  } finally {
    reader.releaseLock();
  }
  expect(received).toBe(size);
  const raw = await transport.fetch(download.url, {
    headers: { Range: "bytes=-4" },
  });
  expect(raw.status).toBe(206);
  expect(await raw.text()).toBe("TAIL");
  for (const [method, status] of [
    ["GET", 404],
    ["HEAD", 405],
  ] as const) {
    await expect(
      transport.fetch(
        `/api/host/files/content?${new URLSearchParams({ path })}`,
        { method },
      ),
    ).rejects.toMatchObject({ status });
  }
  const threadId = (
    await result(transport.client.POST("/api/threads", { body: {} }))
  ).thread_id;
  await expect(
    captureSource(transport, threadId, { file: reviewed }),
  ).rejects.toMatchObject({ status: 413 });
  await writeFile(path, "changed after review");
  expect((await fetch(url, { method: "HEAD" })).status).toBe(409);
  await expect(
    fileTransfer(transport, path, reviewed.entry.revision, "inline"),
  ).rejects.toMatchObject({ status: 409 });
  const html = joinPath(app.native_root, "page.html");
  await writeFile(html, "<script>active content</script>");
  const detached = await fileTransfer(
    transport,
    html,
    (await read(html)).entry.revision,
    "inline",
  );
  const document = await fetch(new URL(detached.url, app.origin));
  expect(document.headers.get("content-disposition")).toMatch(/^attachment;/);
  expect(document.headers.get("content-type")).toBe("application/octet-stream");
  expect(document.headers.get("content-security-policy")).toContain("sandbox");
  expect(await document.text()).toBe("<script>active content</script>");
});

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
  const access = await fileTransfer(
    transport,
    uploadPath,
    uploaded.entry.revision,
    "attachment",
  );
  const downloaded = await fetch(new URL(access.url, app.origin));
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
      a.addAttachment(capture.attachment.attachment_id);
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
