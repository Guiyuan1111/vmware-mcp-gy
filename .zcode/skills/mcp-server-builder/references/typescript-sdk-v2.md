# TypeScript SDK v2（`@modelcontextprotocol/server`）API 详解

> 核验于 2026-09-25，对应 `@modelcontextprotocol/server` 2.1.0、MCP 规范 2026-07-28。
> 来源：[npm: @modelcontextprotocol/server](https://www.npmjs.com/package/@modelcontextprotocol/server) · [TS v2 官方文档](https://ts.sdk.modelcontextprotocol.io/v2/) · [GitHub 仓库](https://github.com/modelcontextprotocol/typescript-sdk)
> 写代码前通读本文；与本文冲突的旧记忆一律以本文 + 官方文档为准。

## 1. 安装与要求

- Node.js **≥ 20**（也支持 Bun / Deno）
- 工程必须 **ESM**（SDK 只发 ES modules）：`npm pkg set type=module`
- 运行 TS 无需构建步骤：装 `tsx` 直接跑

```bash
mkdir my-server && cd my-server
npm init -y
npm pkg set type=module
npm install @modelcontextprotocol/server zod tsx
mkdir src
```

- schema 库用 **Zod v4**：`import * as z from 'zod/v4'`（SDK 走 Standard Schema，Valibot/ArkType 亦可，本文统一 Zod）。
- 客户端包另装：`@modelcontextprotocol/client`（测试时会用到）。
- **旧单包 `@modelcontextprotocol/sdk`（1.x）是 v1 遗留线**，新项目禁用。

## 2. 服务器骨架（stdio）

```ts
import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';

// 工厂模式：每个连接用 createServer() 造一个新实例
function createServer(): McpServer {
    const server = new McpServer({ name: 'weather', version: '1.0.0' });

    server.registerTool(
        'get-alerts',
        {
            description: 'Get the active weather alerts for a US state',
            inputSchema: z.object({
                state: z.string().length(2).describe('Two-letter US state code, e.g. CA')
            })
        },
        async ({ state }) => {
            const res = await fetch(`https://api.weather.gov/alerts/active?area=${state.toUpperCase()}`);
            if (!res.ok) {
                return { content: [{ type: 'text', text: `API error: HTTP ${res.status}` }], isError: true };
            }
            const lines = /* 解析 res.json() */ [];
            return { content: [{ type: 'text', text: lines.join('\n') }] };
        }
    );

    return server;
}

void serveStdio(createServer);
console.error('weather MCP server running on stdio');   // 只能 stderr！
```

要点：

1. **stdout 是协议通道**。一行 `console.log` 就会损坏 JSON-RPC 流、让宿主断连；日志一律 `console.error`。
2. **`serveStdio(createServer)` 接收工厂函数**（不是实例）：它负责 stdin/stdout 读写并为每个连接调用工厂。低层手动模式（`new StdioServerTransport()` + `server.connect(transport)`）仍存在，但常规开发用工厂 + `serveStdio`。
3. `npx tsx src/index.ts` 即可运行；正确表现是 stderr 一行横幅后挂起等待 stdin。

## 3. Tools（`registerTool`）

签名：`server.registerTool(name, config, handler)`。`inputSchema` 是你写的**唯一 schema** —— SDK 从它派生 JSON Schema、在 handler 前校验参数、并推导 handler 参数类型。

```ts
server.registerTool(
    'search',
    {
        description: 'Search the product catalog',
        inputSchema: z.object({
            query: z.string().describe('Substring to match against product names'),
            limit: z.number().int().max(50).optional()
        })
    },
    async ({ query, limit }) => {
        return { content: [{ type: 'text', text: hits.join('\n') }] };
    }
);
```

- `.describe()` 的文字会进入模型看到的 JSON Schema —— **这是模型了解该参数的唯一文档**，必写。
- 无参数工具省略 `inputSchema`。
- 参数不满足 schema 时 SDK 在 handler 之前拒绝，返回 `isError: true` 的校验错误结果（模型可读并重试）。

### 结构化输出

```ts
server.registerTool(
    'product-details',
    {
        description: 'Look up one product by its exact name',
        inputSchema: z.object({ name: z.string() }),
        outputSchema: z.object({ name: z.string(), price: z.number() })
    },
    async ({ name }) => {
        const p = catalog.find(c => c.name === name);
        if (!p) throw new Error(`No product named ${name}`);
        return {
            content: [{ type: 'text', text: JSON.stringify(p) }],
            structuredContent: { name: p.name, price: p.price }
        };
    }
);
```

- `structuredContent` 在离开服务器前按 `outputSchema` 校验；`isError` 结果跳过该校验。
- 测试断言优先打 `structuredContent`。

### 内容块类型

一个结果可混合：`text`、`image`（base64 `data` + `mimeType`）、`audio`、`resource`（内嵌资源内容，免二次 `resources/read`）、`resource_link`（按 URI 引用不携带字节）。

### 工具注解（annotations）

```ts
server.registerTool('clear-catalog', {
    title: 'Clear the catalog',                       // 展示名
    description: 'Remove every product from the catalog',
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true }
}, async () => { /* ... */ });
```

annotations 是给宿主的行为提示（如只读工具可自动批准、破坏性工具要求确认），不影响 SDK 执行。**删除/覆盖/发送/付费类工具必须声明 `destructiveHint: true`**。

## 4. 错误处理（双轨模型）

```ts
import { McpServer, ProtocolError, ProtocolErrorCode, ResourceNotFoundError, ResourceTemplate } from '@modelcontextprotocol/server';
```

| 写法 | 谁看到 | 效果 |
|------|--------|------|
| 返回 `{ content: [...], isError: true }` | **模型** | 请求成功返回、`isError: true`，文本给模型读并自我修正 |
| handler 里 `throw new Error("消息")` | **模型** | SDK 把异常消息转成同样的 `isError: true` 结果 |
| 资源/提示回调 `throw new ProtocolError(code, message, data?)` | **宿主程序** | JSON-RPC 错误响应，模型不可见 |
| 资源回调 `throw new ResourceNotFoundError(uri.href)` | **宿主程序** | `-32602`，`data` 携带 URI |

- **工具 handler 抛出的任何异常（含 ProtocolError）都会被转成 isError 工具错误**，唯一例外 `UrlElicitationRequiredError`。
- 消息里写修复建议：`throw new Error(\`No note with id "${id}". Known ids: ${[...notes.keys()].join(', ')}\`)`。
- 判断口诀与其他细则同 Python 侧：**模型能自我修复 → 工具错误；不能 → 协议错误**。

`ProtocolErrorCode` 速查：

| 成员 | 码 | 含义 |
|------|----|------|
| `ParseError` | -32700 | 非法 JSON |
| `InvalidRequest` | -32600 | 非法 JSON-RPC 请求 |
| `MethodNotFound` | -32601 | 无对应 handler |
| `InvalidParams` | -32602 | 参数错误；也是 `resources/read` 未命中的码 |
| `InternalError` | -32603 | 回调抛了非 ProtocolError 异常 |
| `MissingRequiredClientCapability` | -32021 | 客户端未声明所需能力（2026-07-28 新增） |
| `UnsupportedProtocolVersion` | -32022 | 协议版本不支持（2026-07-28 新增） |
| `UrlElicitationRequired` | -32042 | 需要用户先访问 URL |

## 5. Resources（`registerResource`）

```ts
import { ResourceTemplate } from '@modelcontextprotocol/server';

// 静态资源
server.registerResource(
    'config', 'config://app',
    { title: 'Application Config', description: 'Application configuration data', mimeType: 'text/plain' },
    async uri => ({ contents: [{ uri: uri.href, text: 'log_level=info' }] })
);

// 模板资源（list: undefined 表示实例无界不可枚举）
server.registerResource(
    'user-profile',
    new ResourceTemplate('users://{userId}/profile', { list: undefined }),
    { description: 'Profile data for one user', mimeType: 'application/json' },
    async (uri, { userId }) => ({
        contents: [{ uri: uri.href, mimeType: 'application/json', text: JSON.stringify({ userId }) }]
    })
);
```

- 读回调返回 `{ contents: [...] }`；每项自带 `uri` + `text` 或 base64 `blob`。
- 模板要出现在 `resources/list` 需给 `list` 回调；否则只可通过 `resources/templates/list` 发现模式。

**文件路径防穿越（必做）**：

```ts
import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';

const DOCS_ROOT = path.resolve('./docs');

async (uri, { file }) => {
    const requested = await realpath(path.join(DOCS_ROOT, String(file)));
    if (!requested.startsWith(DOCS_ROOT + path.sep)) {
        throw new Error(`${uri.href} resolves outside the docs root`);
    }
    return { contents: [{ uri: uri.href, text: await readFile(requested, 'utf8') }] };
}
```

`..` 与符号链接都会被 `realpath` 折叠，再比较真实路径前缀 —— 永远不要拿客户端传来的字符串直接读文件。

## 6. Prompts（`registerPrompt`）

```ts
server.registerPrompt(
    'review-code',
    {
        title: 'Code Review',
        description: 'Review code for best practices and potential issues',
        argsSchema: z.object({ code: z.string().describe('The code to review') })
    },
    ({ code }) => ({
        messages: [{ role: 'user' as const, content: { type: 'text', text: `Please review this code:\n\n${code}` } }]
    })
);
```

返回 `{ messages: [...] }`，角色为 `'user' | 'assistant'`（用 `as const` 保住字面量类型）。

## 7. Streamable HTTP 服务（远程部署）

```ts
import { createMcpHandler, McpServer } from '@modelcontextprotocol/server';
import { toNodeHandler } from '@modelcontextprotocol/node';   // npm i @modelcontextprotocol/node

const handler = createMcpHandler(createServer);   // handler.fetch 是 web 标准 (Request)=>Promise<Response>

// Node http 挂载（含 localhost Host/Origin 校验）
import http from 'node:http';
import { localhostHostValidation, localhostOriginValidation } from '@modelcontextprotocol/node';

const nodeHandler = toNodeHandler(handler);
const validateHost = localhostHostValidation();
const validateOrigin = localhostOriginValidation();
http.createServer((req, res) => {
    if (!validateHost(req, res) || !validateOrigin(req, res)) return;
    void nodeHandler(req, res);
}).listen(3000, '127.0.0.1');
```

要点：

- **工厂每请求执行一次**（无状态、可水平扩展）：工具/资源/提示注册必须写在工厂内部；连接池/缓存放模块级闭包。
- 工厂可解构 `{ era, authInfo, requestInfo }`：`authInfo` 由 `handler.fetch(request, { authInfo })` 传入（token 校验在 handler 之前做，SDK 不读不验 token）。
- handler **不校验 Host/Origin、不验 token** —— 部署时必须在外层挂校验（框架包 `@modelcontextprotocol/express|fastify|hono` 的工厂默认已开启 localhost 校验）。
- `createMcpHandler(factory, { responseMode: 'json' | 'sse' })` 固定响应形态；默认按需升级 SSE。
- 关停：`process.on('SIGINT', async () => { await handler.close(); process.exit(0); })`。

## 8. 测试（进程内，无端口）

```ts
import assert from 'node:assert/strict';
import { Client, StreamableHTTPClientTransport, InMemoryTransport } from '@modelcontextprotocol/client';
import { createMcpHandler, McpServer } from '@modelcontextprotocol/server';

// 方式 A：handler.fetch 直连（覆盖 2026-07-28 新协议路径）
const handler = createMcpHandler(createServer);
const transport = new StreamableHTTPClientTransport(new URL('http://test.local/mcp'), {
    fetch: (url, init) => handler.fetch(new Request(url, init))
});
const client = new Client({ name: 'test-harness', version: '1.0.0' }, { versionNegotiation: { mode: 'auto' } });
await client.connect(transport);

const result = await client.callTool({ name: 'apply-discount', arguments: { price: 80, percent: 25 } });
assert.deepStrictEqual(result.structuredContent, { total: 60 });

const failed = await client.callTool({ name: 'apply-discount', arguments: { price: -5, percent: 25 } });
assert.equal(failed.isError, true);        // 失败是普通结果，不是 throw

// 清理：先 client 后 handler
await client.close();
await handler.close();

// 方式 B：InMemoryTransport 成对连接（2025-era 实例）
const [cT, sT] = InMemoryTransport.createLinkedPair();
const memServer = createServer();
const memClient = new Client({ name: 'harness', version: '1.0.0' });
await memServer.connect(sT);
await memClient.connect(cT);

// 方式 C：真实 stdio 冒烟
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
const stdioClient = new Client({ name: 'harness', version: '1.0.0' });
await stdioClient.connect(new StdioClientTransport({ command: 'node', args: ['src/index.ts'] }));
```

测试跑在 Node 自带 `node:test` / `vitest` 均可；`handler.fetch` 方式与线上部署走同一条代码路径，优先用 A。

## 9. 速查：v1 → v2 对照

| v1（`@modelcontextprotocol/sdk`） | v2（`@modelcontextprotocol/server`） |
|---|---|
| `new McpServer({name, version}).tool(...)` | `server.registerTool(name, config, handler)` |
| `server.resource(...)` | `server.registerResource(name, uri, config, cb)` |
| `server.prompt(...)` | `server.registerPrompt(name, config, cb)` |
| `StdioServerTransport` + `connect()` | `serveStdio(createServer)` 工厂 |
| `StreamableHTTPServerTransport` 逐请求接线 | `createMcpHandler(factory)` |
| `McpError` / `ErrorCode` | `ProtocolError` / `ProtocolErrorCode` |
| `zod`（v3 风格导入） | `zod/v4`（Standard Schema） |

官方提供 codemod：`npx @modelcontextprotocol/codemod`（详见 [升级指南](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/migration/upgrade-to-v2.md)）。
