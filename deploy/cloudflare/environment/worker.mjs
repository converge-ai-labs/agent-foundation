import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';

const port = 8080;
const idleMilliseconds = 300_000;
const relayURL = 'http://envd-control.internal/eip';
const identifier = /^[a-zA-Z0-9_-]{1,128}$/;
const routeHeader = 'x-a13n-environment';
const eipPaths = new Set(['/eip/control', '/eip/transfer']);
const missing = () => new Response(null, { status: 404 });
const conflict = () => new Response(null, { status: 409 });
const unavailable = () => new Response(null, { status: 503 });

export default {
  fetch(request, env) {
    if (!env.GATEWAY_TOKEN || request.headers.get('authorization') !== `Bearer ${env.GATEWAY_TOKEN}`) {
      return missing();
    }
    const path = new URL(request.url).pathname;
    if (!eipPaths.has(path) && !['/v1/environment', '/v1/environment/renew'].includes(path)) return missing();
    const id = request.headers.get(routeHeader);
    if (!identifier.test(id ?? '')) return missing();
    return env.ENVIRONMENTS.getByName(id).fetch(request);
  },
};

export class ReverseRelay extends WorkerEntrypoint {
  fetch(request) {
    if (request.url !== relayURL || request.method !== 'GET' ||
        request.headers.get('upgrade')?.toLowerCase() !== 'websocket' ||
        request.headers.get('sec-websocket-protocol') !== 'eip.v1') return missing();
    return this.env.ENVIRONMENTS.getByName(this.ctx.props.id).fetch(
      new Request('https://internal/reverse', request),
    );
  }
}

export class Environment extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    if (ctx.container.running) {
      void ctx.blockConcurrencyWhile(() => ctx.container.setInactivityTimeout(idleMilliseconds));
    }
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.hostname === 'internal' && url.pathname === '/reverse') return this.reverse(request);
    if (request.headers.get('authorization') !== `Bearer ${this.env.GATEWAY_TOKEN}`) return missing();
    try {
      if (request.method === 'PUT' && url.pathname === '/v1/environment') {
        const body = await request.json();
        return await this.ctx.blockConcurrencyWhile(() => this.create(request.headers.get(routeHeader), body));
      }
      if (request.method === 'DELETE' && url.pathname === '/v1/environment') {
        return await this.ctx.blockConcurrencyWhile(() => this.destroy());
      }
      const record = await this.ctx.storage.get('environment');
      const status = await this.status(record);
      if (request.method === 'GET' && url.pathname === '/v1/environment') return Response.json({ status });
      if (status === 'absent') return new Response(null, { status: 410 });
      if (status !== 'running') return unavailable();
      if (request.method === 'POST' && url.pathname === '/v1/environment/renew') {
        await this.ctx.container.setInactivityTimeout(idleMilliseconds);
        return Response.json({ expires_at: new Date(Date.now() + idleMilliseconds).toISOString() });
      }
      if (record.mode !== 'envd_http' || !eipPaths.has(url.pathname)) return missing();
      const headers = new Headers(request.headers);
      headers.delete(routeHeader);
      headers.set('authorization', `Bearer ${record.localToken}`);
      return await this.ctx.container.getTcpPort(port).fetch(
        new Request(`http://container${url.pathname}${url.search}`, new Request(request, { headers, redirect: 'manual' })),
      );
    } catch {
      return unavailable();
    }
  }

  async status(record) {
    if (!record || record.phase === 'deleted') return 'absent';
    const container = this.ctx.container;
    if (!container.running) return 'absent';
    const info = await container.inspect();
    if (!container.running) return 'absent';
    if (!info?.image) return 'pending';
    if (info.labels['a13n.operation'] !== record.operation) throw new Error('Wrong container');
    return 'running';
  }

  async create(id, body) {
    if (!identifier.test(body?.operation ?? '') || !['envd_http', 'envd_ws'].includes(body.mode) ||
        !['public', 'disabled'].includes(body.network_egress)) {
      return new Response(null, { status: 400 });
    }
    const existing = await this.ctx.storage.get('environment');
    if (existing) {
      if (existing.operation !== body.operation || existing.mode !== body.mode || existing.egress !== body.network_egress || existing.phase === 'deleted') {
        return conflict();
      }
      const status = await this.status(existing);
      if (status !== 'absent' || existing.phase === 'started') return Response.json({ status });
    } else if (this.ctx.container.running) {
      return conflict();
    }
    const outbound = body.mode === 'envd_ws' ? this.outbound(id, body.outbound) : null;
    if (body.mode === 'envd_ws' && !outbound) return new Response(null, { status: 400 });
    const record = existing ?? {
      phase: 'reserved', operation: body.operation, mode: body.mode, egress: body.network_egress, outbound,
      envdConfig: body.A13N_ENVD_CONFIG_JSON,
      localToken: crypto.randomUUID() + crypto.randomUUID(),
    };
    await this.ctx.storage.put('environment', record);
    const c = this.ctx.container;
    // Set up the hostname-specific intercept before starting the Envd process.
    if (record.outbound) {
      await c.interceptOutboundHttp('envd-control.internal', this.ctx.exports.ReverseRelay({ props: { id } }));
    }
    const env = {
      ...(record.envdConfig ? { A13N_ENVD_CONFIG_JSON: record.envdConfig } : {}),
      A13N_ENVD_DEVICE_ID: id,
      A13N_ENVD_DEFAULT_WORKING_DIRECTORY: '/workspace',
      ...(record.outbound ? {
        A13N_ENVD_TRANSPORT: 'reverse_websocket',
        A13N_ENVD_REVERSE_WS_URL: relayURL.replace('http:', 'ws:'),
        A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE: '/var/lib/a13n-envd/service-token',
        A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN: record.localToken,
      } : {
        A13N_ENVD_TRANSPORT: 'http',
        A13N_ENVD_HTTP_BIND: `0.0.0.0:${port}`,
        A13N_ENVD_HTTP_PLAINTEXT_SCOPE: 'provider_private_link',
        A13N_ENVD_HTTP_CREDENTIAL_FILE: '/var/lib/a13n-envd/http-token',
        A13N_CLOUDFLARE_HTTP_TOKEN: record.localToken,
      }),
    };
    // Persist dispatch before starting: a crash here can lose an allocation,
    // but cannot relaunch an already lost workspace under the same identity.
    await this.ctx.storage.put('environment', { ...record, phase: 'started' });
    c.start({ image: c.images.base, instance: 'lite', enableInternet: record.egress === 'public', env,
      entrypoint: ['/usr/bin/tini', '--', '/usr/local/bin/a13n-cloudflare-launch', 'a13n-envd'],
      labels: { 'a13n.operation': record.operation } });
    await c.setInactivityTimeout(idleMilliseconds);
    return Response.json({ status: 'pending' });
  }

  outbound(id, binding) {
    if (!this.env.SERVICE_ORIGIN || typeof binding?.token !== 'string' ||
        !/^[a-zA-Z0-9_-]{16,512}$/.test(binding.token)) return null;
    const url = new URL(`/api/v1/environments/${id}/connect`, this.env.SERVICE_ORIGIN);
    const supplied = URL.parse(binding.url);
    if (url.protocol !== 'https:' || !supplied || supplied.href !== url.href.replace('https:', 'wss:')) return null;
    return { url: url.href, token: binding.token };
  }

  async reverse(request) {
    const record = await this.ctx.storage.get('environment');
    if (!record?.outbound || record.phase === 'deleted' || !this.ctx.container.running ||
        request.headers.get('authorization') !== `Bearer ${record.localToken}`) return missing();
    const headers = new Headers({
      'authorization': `Bearer ${record.outbound.token}`, 'upgrade': 'websocket',
      'sec-websocket-protocol': 'eip.v1',
    });
    return fetch(record.outbound.url, { headers, redirect: 'manual' });
  }

  async destroy() {
    const record = await this.ctx.storage.get('environment');
    const c = this.ctx.container;
    if (c.running) {
      if (!record || await this.status(record) !== 'running') return unavailable();
      await c.destroy();
      if (c.running) return unavailable();
    }
    // Keep a tombstone to prevent a retry from reviving this Environment ID.
    await this.ctx.storage.put('environment', { phase: 'deleted' });
    return new Response(null, { status: 204 });
  }
}
