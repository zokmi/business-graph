import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';

const [executable, ...args] = process.argv.slice(2);

try {
  if (!executable) throw new Error('請指定 MCP 後端執行檔及啟動參數。');
  while (true) {
    const child = spawn(executable, args, {
      windowsHide: true,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    // The service host owns the process tree and stops the backend with Node.
    const [code, signal] = await once(child, 'exit');
    console.error(`MCP 後端已結束（${signal ?? code}），5 秒後重新啟動。`);
    await delay(5000);
  }
} catch (error) {
  console.error(`MCP 後端啟動失敗：${error.message}`);
  process.exitCode = 1;
}
