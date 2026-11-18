import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { open, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { isAbsolute, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const launcherArgs = process.argv.slice(2);
const headersOnly = launcherArgs[0] === '--headers';
const doctor = launcherArgs[0] === '--doctor';
if (headersOnly || doctor) launcherArgs.shift();
const [portArgument, stateDirectory, executable, ...args] = launcherArgs;
const port = Number(portArgument);
const token = process.env.BUSINESS_GRAPH_MCP_TOKEN;
const fingerprint = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const processQuery = `
$ErrorActionPreference = 'Stop'
$process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$env:BGRAPH_DOCTOR_PID)
if ($null -eq $process) { exit 3 }
if (!$process.ExecutablePath -or !$process.CommandLine -or !$process.CreationDate) { exit 4 }
$created = $process.CreationDate.ToUniversalTime()
$value = @($created.ToString('o'), $process.ExecutablePath, $process.CommandLine) | ConvertTo-Json -Compress
$hash = [Security.Cryptography.SHA256]::Create()
$identity = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($value))).Replace('-', '')
$hash.Dispose()
if ($env:BGRAPH_DOCTOR_IDENTITY) {
  if ($identity -cne $env:BGRAPH_DOCTOR_IDENTITY) { exit 4 }
  $netstat = & "$env:SystemRoot/System32/netstat.exe" -ano
  if ($LASTEXITCODE -ne 0) { exit 5 }
  $listeners = $netstat | Select-String ("^\\s*TCP\\s+\\S+:" + [int]$env:BGRAPH_DOCTOR_PORT + "\\s+(?:0\\.0\\.0\\.0|\\[::\\]):0\\s+\\S+\\s+(\\d+)\\s*$")
  foreach ($listener in $listeners) {
    if ([int]$listener.Matches[0].Groups[1].Value -ne [int]$env:BGRAPH_DOCTOR_PID) { exit 5 }
  }
  if ($env:BGRAPH_DOCTOR_REQUIRE_LISTENER -eq '1' -and !$listeners) { exit 5 }
  $current = Get-Process -Id ([int]$env:BGRAPH_DOCTOR_PID)
  $null = $current.Handle
  if ([Math]::Abs(($current.StartTime.ToUniversalTime() - $created).Ticks) -ge 10) { exit 4 }
  Stop-Process -InputObject $current -ErrorAction Stop
  if (!$current.WaitForExit(5000)) { exit 6 }
} else {
  $identity
}
`;

function queryProcess(pid, identity, requireListener = false) {
  const env = { ...process.env, BGRAPH_DOCTOR_PID: String(pid), BGRAPH_DOCTOR_PORT: String(port),
    BGRAPH_DOCTOR_IDENTITY: identity || '', BGRAPH_DOCTOR_REQUIRE_LISTENER: requireListener ? '1' : '0' };
  delete env.BUSINESS_GRAPH_MCP_TOKEN;
  return spawnSync(join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    ['-NoProfile', '-NonInteractive', '-Command', processQuery], { env, encoding: 'utf8', timeout: 8000, windowsHide: true });
}

async function isReady(url) {
  const health = await fetch(`${url}/healthz`, {
    redirect: 'error', signal: AbortSignal.timeout(1000),
  }).catch((error) => {
    if (error.cause?.code === 'ECONNREFUSED') return null;
    throw error;
  });
  if (!health) return false;
  if (doctor && [502, 503].includes(health.status)) return 'unavailable';
  if (!health.ok || (await health.json()).status !== 'ok') {
    throw new Error(doctor ? 'DOCTOR_PORT' : '連接埠已被其他服務使用。');
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
  if (doctor && [502, 503].includes(response.status)) return 'unavailable';
  if (!response.ok) throw new Error(doctor ? 'DOCTOR_AUTH' : `MCP 驗證失敗（HTTP ${response.status}）。`);
  const { result } = await response.json();
  if (!result?.tools?.some(({ name }) => name === 'business_explore')) {
    throw new Error(doctor ? 'DOCTOR_AUTH' : '此端點未提供 business-graph MCP 工具。');
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
    const ready = await isReady(url);
    if (ready === true) return url;
    const identityPath = join(stateDirectory, 'doctor-process.json');
    const configuration = fingerprint([launcherArgs, token]);
    if (doctor) {
      let record;
      try {
        record = JSON.parse(await readFile(identityPath, 'utf8'));
      } catch (error) {
        if (error.code !== 'ENOENT') throw new Error('DOCTOR_IDENTITY');
      }
      if (ready === 'unavailable') {
        await delay(500);
        const confirmation = await isReady(url);
        if (confirmation === true) return url;
        if (confirmation !== 'unavailable') throw new Error('DOCTOR_PORT');
      }
      if (record && Number.isInteger(record.pid) && record.pid > 0 && typeof record.identity === 'string' && record.identity) {
        const current = queryProcess(record.pid);
        if (current.status === 0 && current.stdout.trim() === record.identity) {
          if (record.configuration !== configuration) throw new Error('DOCTOR_CONFIG');
          if (queryProcess(record.pid, record.identity, ready === 'unavailable').status !== 0) {
            throw new Error('DOCTOR_PORT');
          }
        } else if (current.status !== 3 || ready === 'unavailable') {
          throw new Error('DOCTOR_IDENTITY');
        }
      } else if (record || ready === 'unavailable') {
        throw new Error('DOCTOR_IDENTITY');
      }
    }
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
      if (await isReady(url) === true) {
        const processIdentity = queryProcess(child.pid);
        if (processIdentity.status === 0 && processIdentity.stdout.trim()) {
          const temporaryIdentityPath = `${identityPath}.${process.pid}.tmp`;
          await writeFile(temporaryIdentityPath, JSON.stringify({
            pid: child.pid, identity: processIdentity.stdout.trim(), configuration,
          })).then(() => rename(temporaryIdentityPath, identityPath)).catch(() => {
            console.error('本機程序已就緒，無法儲存供下次 doctor 重啟使用的身分紀錄。');
          });
        }
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
