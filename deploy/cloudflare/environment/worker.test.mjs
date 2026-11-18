import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { test } from 'node:test';

registerHooks({ resolve(specifier, context, next) {
  if (specifier === 'cloudflare:workers') {
    return { shortCircuit: true, url: 'data:text/javascript,export class DurableObject { constructor(ctx,env){this.ctx=ctx;this.env=env} }; export class WorkerEntrypoint {}' };
  }
  return next(specifier, context);
} });
const { default: gateway, Environment } = await import('./worker.mjs');

function fixture() {
  const records = new Map();
  const starts = [];
  const forwarded = [];
  const container = {
    running: false, images: { base: 'base' },
    start(options) { starts.push(options); this.running = true; },
    async inspect() { return this.running ? starts.at(-1) : null; },
    async destroy() { this.running = false; },
    async interceptOutboundHttp(host) { assert.equal(host, 'envd-control.internal'); },
    async setInactivityTimeout(ms) { assert.equal(ms, 300_000); },
    getTcpPort(port) {
      assert.equal(port, 8080);
      return { async fetch(request) {
        forwarded.push(request.headers);
        return new Response(request.body);
      } };
    },
  };
  const env = { GATEWAY_TOKEN: 'fake-gateway-token', SERVICE_ORIGIN: 'https://service.example' };
  const ctx = {
    container,
    storage: { get: key => records.get(key), put: (key, value) => records.set(key, value) },
    blockConcurrencyWhile: f => f(),
    exports: { ReverseRelay: () => ({}) },
  };
  const instance = new Environment(ctx, env);
  env.ENVIRONMENTS = { getByName(id) { assert.equal(id, 'env-test'); return instance; } };
  async function call(method, path, { body, authorized = true } = {}) {
    return gateway.fetch(new Request(`https://gateway.example${path}`, {
      method, body, headers: { 'x-a13n-environment': 'env-test',
        ...(authorized ? { authorization: 'Bearer fake-gateway-token' } : {}) },
    }), env);
  }
  return { call, starts, container, forwarded, instance };
}

const path = '/v1/environment';
for (const [mode, policy] of [['envd_http', 'public'], ['envd_ws', 'disabled']]) {
  test(`${mode}: frozen egress, one allocation, no wake after loss or deletion`, async () => {
    const { call, starts, container } = fixture();
    const body = JSON.stringify({ operation: 'op-test', mode, network_egress: policy,
      ...(mode === 'envd_ws' ? { outbound: {
        url: 'wss://SERVICE.example:443/api/v1/environments/env-test/connect', token: 'fake-outbound-token',
      } } : {}),
    });
    assert.equal((await call('PUT', path, { body, authorized: false })).status, 404);
    assert.equal((await (await call('PUT', path, { body })).json()).status, 'pending');
    assert.equal((await (await call('PUT', path, { body })).json()).status, 'running');
    assert.equal(starts.length, 1);
    assert.equal(starts[0].enableInternet, policy === 'public');
    assert.equal(starts[0].image, 'base');
    assert.equal(starts[0].entrypoint, undefined);
    assert.ok(!JSON.stringify(starts[0]).includes('fake-outbound-token'));
    assert.equal((await call('POST', `${path}/renew`)).status, 200);
    container.running = false;
    assert.equal((await call('POST', `${path}/renew`)).status, 410);
    assert.equal((await (await call('PUT', path, { body })).json()).status, 'absent');
    assert.equal((await call('DELETE', path)).status, 204);
    assert.equal((await call('DELETE', path)).status, 204);
    assert.equal((await call('PUT', path, { body })).status, 409);
    assert.equal(starts.length, 1);
  });
}

test('HTTP binary forwarding uses only the per-container credential', async () => {
  const { call, forwarded, starts } = fixture();
  const body = JSON.stringify({ operation: 'op-test', mode: 'envd_http', network_egress: 'public' });
  await call('PUT', path, { body });
  const bytes = Uint8Array.of(255, 0, 128, 1, 2);
  const response = await call('POST', '/eip/transfer?cursor=abc', { body: bytes });
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
  assert.equal(forwarded[0].get('authorization'),
    `Bearer ${starts[0].env.A13N_ENVD_HTTP_BOOTSTRAP_TOKEN}`);
  assert.equal(forwarded[0].has('x-a13n-environment'), false);
});

test('reverse relay uses pinned Service route and retains the upstream credential outside the guest', async t => {
  const { call, starts, instance } = fixture();
  const body = JSON.stringify({ operation: 'op-test', mode: 'envd_ws', network_egress: 'disabled',
    outbound: { url: 'wss://SERVICE.example:443/api/v1/environments/env-test/connect', token: 'fake-outbound-token' },
  });
  await call('PUT', path, { body });
  const response = new Response(null, { status: 200 });
  const upstream = t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'https://service.example/api/v1/environments/env-test/connect');
    assert.equal(options.headers.get('authorization'), 'Bearer fake-outbound-token');
    assert.equal(options.headers.get('sec-websocket-protocol'), 'eip.v1');
    assert.equal(options.redirect, 'manual');
    return response;
  });
  function reverse(token) {
    return instance.reverse(new Request('https://internal/reverse', {
      headers: { authorization: `Bearer ${token}` },
    }));
  }
  assert.equal((await reverse('wrong')).status, 404);
  assert.strictEqual(await reverse(starts[0].env.A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN), response);
  assert.equal(upstream.mock.callCount(), 1);
});
