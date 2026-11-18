import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, isAbsolute, join } from 'node:path';
import { fileURLToPath } from 'node:url';

function recover() {
  const configPath = join(process.env.CLAUDE_CONFIG_DIR || homedir(), '.claude.json');
  const config = JSON.parse(readFileSync(configPath, 'utf8').replace(/^\uFEFF/, ''));
  const server = config.mcpServers?.['business-graph'];
  if (!server?.url || typeof server.headersHelper !== 'string') {
    throw new Error('找不到 business-graph 的使用者層級 HTTP MCP 與 headersHelper 設定。');
  }

  const endpoint = new URL(server.url);
  if (endpoint.protocol !== 'http:' || endpoint.hostname !== '127.0.0.1'
      || endpoint.pathname !== '/mcp' || endpoint.username || endpoint.password
      || endpoint.search || endpoint.hash) {
    throw new Error('doctor 需要 http://127.0.0.1:<port>/mcp 的本機設定。');
  }

  const [node, launcher, mode, port, stateDir, executable, ...args] =
    [...server.headersHelper.matchAll(/"([^"]*)"|([^\s"]+)/g)].map((match) => match[1] ?? match[2]);
  if (!/^node(?:\.exe)?$/i.test(basename(node || '')) || !launcher || !isAbsolute(launcher)
      || basename(launcher) !== 'start-local-mcp.mjs' || mode !== '--headers'
      || port !== (endpoint.port || '80') || !stateDir || !isAbsolute(stateDir) || !executable) {
    throw new Error('headersHelper 的參數與 business-graph 本機啟動器不相符。');
  }
  if (!process.env.BUSINESS_GRAPH_MCP_TOKEN) {
    throw new Error('缺少 BUSINESS_GRAPH_MCP_TOKEN 環境變數。');
  }

  const bundledLauncher = fileURLToPath(new URL('../../scripts/start-local-mcp.mjs', import.meta.url));
  const result = spawnSync(process.execPath, [bundledLauncher, '--doctor', port, stateDir, executable, ...args], {
    encoding: 'utf8',
    timeout: 45000,
    windowsHide: true,
  });
  if (result.error?.code === 'ETIMEDOUT') {
    throw new Error('等待 business-graph MCP 啟動器逾時。');
  }
  if (result.error || result.status !== 0) {
    const diagnostic = result.stderr?.match(/DOCTOR_[A-Z_]+/)?.[0];
    const messages = {
      DOCTOR_IDENTITY: '程序身分無法驗證，請檢查現有部署。',
      DOCTOR_CONFIG: '啟動設定或 token 已變更，請檢查安裝設定。',
      DOCTOR_PORT: '無法確認連接埠歸屬或停止已識別的程序。',
      DOCTOR_AUTH: 'MCP 工具驗證失敗，請檢查 token 與本機設定。',
    };
    if (diagnostic && messages[diagnostic]) throw new Error(messages[diagnostic]);
    throw new Error('MCP 啟動或工具驗證失敗。請在本機檢查狀態目錄的 server.log。');
  }
  console.log('business-graph MCP 已通過健康與工具驗證。');
}

try {
  recover();
} catch (error) {
  const message = error instanceof SyntaxError || error instanceof TypeError
    ? '無法解析 Claude Code 的 business-graph 設定。'
    : error.code ? '無法讀取 Claude Code 使用者設定。' : error.message;
  console.error(`business-graph doctor：${message}`);
  process.exitCode = 1;
}
