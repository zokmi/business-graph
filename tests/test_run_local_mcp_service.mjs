import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const service = fileURLToPath(new URL('../scripts/run-local-mcp-service.mjs', import.meta.url));

test('Node service restarts the backend after it exits', { timeout: 15_000 }, async () => {
  const backend = "process.stdout.write(process.pid + '\\n'); process.exitCode = 7";
  const child = spawn(process.execPath, [service, process.execPath, '-e', backend], {
    windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'],
  });
  const exited = once(child, 'close');
  let output = '';
  try {
    for await (const chunk of child.stdout) {
      output += chunk;
      if (output.split('\n').length >= 3) break;
    }
    const [first, second] = output.trim().split('\n');
    assert.match(second ?? '', /^\d+$/);
    assert.notEqual(second, first);
  } finally {
    child.kill();
    await exited;
  }
});
