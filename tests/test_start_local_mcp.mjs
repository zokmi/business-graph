import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const exec = promisify(execFile);
const launcher = fileURLToPath(new URL('../scripts/start-local-mcp.mjs', import.meta.url));

test('同時啟動共用一個後端，重跑沿用已就緒的服務', {
  skip: process.platform !== 'win32',
}, async () => {
  const directory = await mkdtemp(join(tmpdir(), 'business-graph-start-'));
  const backend = join(directory, 'backend.cjs');
  const starts = join(directory, 'starts.txt');
  const reservation = createServer().listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const { port } = reservation.address();
  await promisify(reservation.close.bind(reservation))();
  await writeFile(backend, `
    const http = require('node:http');
    require('node:fs').appendFileSync(${JSON.stringify(starts)}, process.pid + '\\n');
    setTimeout(() => {
      http.createServer((req, res) => {
        if (req.url === '/stop') {
          res.end();
          return process.exit(0);
        }
        res.setHeader('content-type', 'application/json');
        if (req.url === '/healthz') return res.end('{"status":"ok"}');
        if (req.headers.authorization !== 'Bearer fixture-token') {
          res.statusCode = 401;
          return res.end('{}');
        }
        res.end('{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"business_explore"}]}}');
      }).listen(${port}, '127.0.0.1');
    }, 500);
  `);
  const start = (options = [], token = 'fixture-token') => exec(process.execPath,
    [launcher, ...options, String(port), directory, process.execPath, backend],
    { env: { ...process.env, BUSINESS_GRAPH_MCP_TOKEN: token } });
  try {
    await Promise.all([start(), start(), start()]);
    const { stdout } = await start(['--headers']);
    assert.deepEqual(JSON.parse(stdout), { Authorization: 'Bearer fixture-token' });
    await assert.rejects(start([], 'incorrect-token'), { stderr: /HTTP 401/ });
    assert.equal((await readFile(starts, 'utf8')).trim().split('\n').length, 1);
    await fetch(`http://127.0.0.1:${port}/stop`).catch(() => {});
    await start();
    assert.equal((await readFile(starts, 'utf8')).trim().split('\n').length, 2);
  } finally {
    await fetch(`http://127.0.0.1:${port}/stop`).catch(() => {});
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
});
