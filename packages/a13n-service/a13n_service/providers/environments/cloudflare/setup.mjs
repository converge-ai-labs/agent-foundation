// A single namespace member owns application submission, including uncertain outcomes.
export async function setupApplication(ctx, env, request) {
  const token = request.headers.get("X-A13N-Cloudflare-Token");
  if (!token || token.length > 4096) return new Response(null, { status: 401 });
  const api = async (method, path, body) => {
    const response = await fetch(
      "https://api.cloudflare.com/client/v4/accounts/" +
        env.ACCOUNT_ID +
        "/" +
        path,
      {
        method,
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
        redirect: "error",
        signal: AbortSignal.timeout(20000),
      },
    );
    if (!response.ok) {
      const error = new Error("cloudflare_control_failed");
      error.rejected = [400, 401, 403, 422].includes(response.status);
      throw error;
    }
    const result = await response.json();
    if (!result.success) throw new Error("cloudflare_control_failed");
    return result;
  };
  try {
    let old = await ctx.storage.get("application");
    const matches = [];
    // Explicit bounded pagination; never infer absence from a partial page.
    let pageToken;
    for (let page = 1; page <= 100; page++) {
      const listing = await api(
        "GET",
        "containers/applications" +
          (pageToken ? "?page_token=" + encodeURIComponent(pageToken) : ""),
      );
      if (!Array.isArray(listing.result)) throw new Error("invalid_listing");
      matches.push(...listing.result.filter((x) => x.name === env.SCRIPT_NAME));
      const next = listing.result_info?.next_page_token;
      if (!next) break;
      if (typeof next !== "string" || next === pageToken || page === 100)
        throw new Error("listing_limit");
      pageToken = next;
    }
    if (matches.length > 1)
      return Response.json({ error: "application_conflict" }, { status: 409 });
    if (matches.length === 1) {
      const app = matches[0];
      const namespaces = await api(
        "GET",
        "workers/durable_objects/namespaces?per_page=1000",
      );
      const namespace = namespaces.result?.find(
        (x) => x.script === env.SCRIPT_NAME && x.class === "Environment",
      );
      if (
        !namespace ||
        app.durable_objects?.namespace_id !== namespace.id ||
        app.scheduling_policy !== "durable_object"
      )
        return Response.json(
          { error: "application_conflict" },
          { status: 409 },
        );
      if (old?.id && old.id !== app.id)
        return Response.json(
          { error: "application_replaced" },
          { status: 409 },
        );
      old = { phase: "ready", id: app.id };
      await ctx.storage.put("application", old);
      return Response.json(old);
    }
    if (old)
      return Response.json(
        { error: "application_submission_unconfirmed" },
        { status: 503 },
      );
    await ctx.storage.put("application", { phase: "submitting" });
    let result;
    try {
      result = await api("POST", "containers/applications", {
        name: env.SCRIPT_NAME,
        scheduling_policy: "durable_object",
        durable_objects: {
          script_name: env.SCRIPT_NAME,
          class_name: "Environment",
        },
      });
    } catch (error) {
      // Only a definitive rejection permits retry after permissions/configuration are fixed.
      // Timeout, connection loss, conflict or server errors preserve the submission fence.
      if (error.rejected) await ctx.storage.delete("application");
      throw error;
    }
    // A later invocation verifies the exact namespace binding before use.
    await ctx.storage.put("application", {
      phase: "submitted",
      id: result.result.id,
    });
    return Response.json({ phase: "submitted" }, { status: 202 });
  } catch {
    return Response.json(
      { error: "application_setup_unconfirmed" },
      { status: 503 },
    );
  }
}
