import { DurableObject } from "cloudflare:workers";
import { Controller, authenticate } from "./controller.mjs";
export class Environment extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.controller = new Controller(ctx, env);
    ctx.blockConcurrencyWhile(() => this.controller.initialize());
  }
  fetch(request) {
    return this.controller.fetch(request);
  }
  alarm() {
    return this.controller.alarm();
  }
}
export default {
  async fetch(request, env) {
    if (!(await authenticate(request, env.AUTH_HASH)))
      return new Response(null, { status: 401 });
    const url = new URL(request.url);
    const setup = url.pathname === "/setup";
    const id = setup ? "$setup" : request.headers.get("X-A13N-Environment");
    if (!id || (!/^[A-Za-z0-9_.:-]{1,128}$/.test(id) && !setup))
      return new Response(null, { status: 400 });
    return env.ENVIRONMENTS.getByName(id).fetch(request);
  },
};
