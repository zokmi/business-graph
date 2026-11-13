import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { open, mkdir } from 'node:fs/promises';
import { createServer } from 'node:net';
import { isAbsolute, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const launcherArgs = process.argv.slice(2);
const headersOnly = launcherArgs[0] === '--headers';
if (headersOnly) launcherArgs.shift();
const [portArgument, stateDirectory, executable, ...args] = launcherArgs;
const port = Number(portArgument);
const token = process.env.BUSINESS_GRAPH_MCP_TOKEN;

async function isReady(url) {
  const health = await fetch(`${url}/healthz`, {
    redirect: 'error', signal: AbortSignal.timeout(1000),
  }).catch((error) => {
    if (error.cause?.code === 'ECONNREFUSED') return null;
    throw error;
  });
  if (!health) return false;
  if (!health.ok || (await health.json()).status !== 'ok') {
    throw new Error('連接埠已被其他服務使用。');
  }
  const response = await fetch(`${url}/mcp`, {
    method: 'POST',
    redirect: 'error',
    signal: AbortSignal.timeout(2000),
    headers: {
      Accept: 'application/json, text/event-stream',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  });
  if (!response.ok) throw new Error(`MCP 驗證失敗（HTTP ${response.status}）。`);
  const { result } = await response.json();
  if (!result?.tools?.some(({ name }) => name === 'business_explore')) {
    throw new Error('此端點未提供 business-graph MCP 工具。');
  }
  return true;
}

async function start() {
  if (process.platform !== 'win32' || !Number.isInteger(port) || port < 1 || port > 65535
      || !stateDirectory || !isAbsolute(stateDirectory) || !executable || !token) {
    throw new Error('在 Windows 設定 BUSINESS_GRAPH_MCP_TOKEN，並傳入連接埠、絕對狀態目錄、後端指令及參數。');
  }

  const url = `http://127.0.0.1:${port}`;
  const deadline = performance.now() + 30_000;
  // 管道由作業系統在啟動器結束時回收，異常退出也可重新取得。
  const lock = createServer((socket) => socket.destroy());
  const pipe = `\\\\.\\pipe\\business-graph-mcp-start-${port}`;
  while (true) {
    try {
      lock.listen(pipe);
      await once(lock, 'listening');
      break;
    } catch (error) {
      if (error.code !== 'EADDRINUSE' || performance.now() >= deadline) throw error;
      await delay(100);
    }
  }

  let child;
  try {
    if (await isReady(url)) return url;
    await mkdir(stateDirectory, { recursive: true });
    const log = await open(join(stateDirectory, 'server.log'), 'a');
    try {
      const env = { ...process.env };
      delete env.BUSINESS_GRAPH_MCP_TOKEN;
      child = spawn(executable, args, {
        cwd: stateDirectory,
        detached: true,
        windowsHide: true,
        stdio: ['ignore', log.fd, log.fd],
        env,
      });
      await once(child, 'spawn');
    } finally {
      await log.close();
    }
    while (performance.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error('後端在就緒前結束，請檢查 server.log。');
      }
      if (await isReady(url)) {
        child.unref();
        return url;
      }
      await delay(100);
    }
    throw new Error('等待後端就緒逾時，請檢查 server.log。');
  } catch (error) {
    if (child?.pid) child.kill();
    throw error;
  } finally {
    lock.close();
  }
}

try {
  const url = await start();
  if (headersOnly) {
    console.log(JSON.stringify({ Authorization: `Bearer ${token}` }));
  } else {
    console.log(`business-graph MCP 已就緒 ${url}/mcp`);
  }
} catch (error) {
  console.error(`business-graph MCP 啟動失敗：${error.cause?.code ?? error.message}`);
  process.exitCode = 1;
}
