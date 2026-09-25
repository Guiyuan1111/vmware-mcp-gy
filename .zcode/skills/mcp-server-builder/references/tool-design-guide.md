# 工具设计指南 —— 让模型真正会用你的服务器

> MCP 服务器的成败在工具设计：协议正确但描述糟糕的工具 = 模型不会用/用错。
> 本文是 SKILL.md Step 3a 的展开。代码示例为 TS（`zod/v4`），Python 写法对照见各 SDK 文档。

## 1. Description 是工具的全部"UI"

模型在 `tools/list` 时看到的只有：名称、description、参数 schema（含每个字段的 describe）。它对工具的一切理解都来自这三样。

**好的 description 回答四个问题**：做什么？什么时候该用（以及不该用）？参数什么含义？返回什么？

```ts
// ❌ 坏：模型无从判断何时用它
{ description: 'Search issues' }

// ✅ 好：场景 + 边界 + 返回说明
{
    description: 'List open issues in a GitHub repository, newest first. ' +
        'Returns number, title, author, and updated time per issue. ' +
        'Use get-issue instead when you already know the issue number.'
}
```

**每字段必写 describe**：

```ts
inputSchema: z.object({
    repo: z.string().describe('Repository as "owner/name", e.g. "facebook/react"'),
    labels: z.array(z.string()).optional().describe('Filter by these label names'),
    limit: z.number().int().min(1).max(100).default(20).describe('Max issues to return')
})
```

**工具命名**：小写 kebab-case、动词开头（`get-`/`list-`/`search-`/`create-`/`send-`/`convert-`）、作用域清晰。同一服务器内保持统一前缀风格。名称是模型匹配意图的第一信号，`fetch_data` 这种万能名是反模式。

## 2. Schema 设计原则

- **必填最小化**：每个必填参数都是模型出错的机会。能推断的给默认值，能合并的合并（如 `repo: "owner/name"` 优于 `owner` + `repo` 两个必填）。
- **约束前置**：`min/max/length/regex` 写进 schema，SDK 会在 handler 前拒绝非法参数并把校验错误还给模型（模型能读懂并重试）。不要在 handler 里重复校验 schema 已表达的限制。
- **枚举优于自由文本**：取值有限就用 `z.enum([...])` / Python `Literal[...]`，模型的参数命中率显著更高。
- **扁平优于嵌套**：JSON Schema 对深层嵌套对象的补全能力弱；能用扁平参数就别造嵌套结构。
- **不要拿 tool 当函数库**：20 个细粒度 getter 不如 3 个按真实任务聚合的工具。判断标准是"模型的一个意图"能否用一个工具完成。

## 3. 返回值设计

- **文本为主**：`content` 是模型唯一阅读的通道。返回结构化、信息密集、可直接引用的文本（列表带编号、日期规范化），不要返回大段原始 JSON —— 模型读 JSON 又慢又容易错位。
- **程序要读就加结构化通道**：宿主程序需要精确值时启用结构化输出（Python 返回类型注解 / TS `outputSchema`+`structuredContent`），同时保留给模型的文本摘要。两通道不是二选一。
- **失败也是返回值设计的一部分**：错误消息 = 给模型的修复指令。写"Known ids: a, b, c"而不是"invalid id"（模板见各 SDK 文档的错误处理节）。
- **大结果截断要声明**：结果可能超过百行时，在 description 里写明默认截断行为并提供 `page`/`limit` 参数；截断时在返回文本末尾标注"（已截断，共 N 条，用 page=2 继续）"。

## 4. 副作用与安全标注

```ts
annotations: {
    title: 'Delete deployment',
    readOnlyHint: false,        // 只读？
    destructiveHint: true,      // 破坏性（删除/覆盖/不可逆）？
    idempotentHint: true,       // 重复调用结果相同？
    openWorldHint: false        // 是否与外部实体（API/网络）交互
}
```

宿主据此决定是否需要用户确认。规则：

- 删除、覆盖、发送（消息/邮件/支付）、花钱类工具**必须** `destructiveHint: true`，且 description 写明不可逆后果。
- 设计"危险操作三件套"：先提供只读的预览/`dry_run` 参数，破坏性操作返回执行摘要而非静默成功。
- 幂等设计：同一参数重复调用产生相同状态（用 upsert 代替裸 create），模型重试时不会制造重复数据。

## 5. 外部 API 集成模式（API 集成类推断项）

```ts
server.registerTool('get-weather', {
    description: 'Current weather for a city, from Open-Meteo',
    inputSchema: z.object({ city: z.string().describe('City name in English') })
}, async ({ city }) => {
    // 1) 凭证：环境变量，缺席时降级而不是崩溃
    const key = process.env.WEATHER_API_KEY;

    // 2) 超时 + 重试（指数退避），429 尊重 Retry-After
    let res: Response;
    for (let attempt = 0; ; attempt++) {
        res = await fetch(url, {
            headers: key ? { Authorization: `Bearer ${key}` } : {},
            signal: AbortSignal.timeout(30_000)
        });
        if (res.status === 429 && attempt < 2) {
            await new Promise(r => setTimeout(r, Number(res.headers.get('retry-after') ?? 2 ** attempt) * 1000));
            continue;
        }
        if (res.ok || attempt >= 2) break;
        await new Promise(r => setTimeout(r, 2 ** attempt * 500));   // 0.5s, 1s
    }

    // 3) 错误分级：模型可修 → isError 文本；不可修 → 抛
    if (res.status === 404) {
        return { content: [{ type: 'text', text: `City "${city}" not found. Check spelling or try a nearby major city.` }], isError: true };
    }
    if (!res.ok) {
        throw new Error(`Weather API HTTP ${res.status} after retries`);
    }

    // 4) 解析防御：字段存在性校验后再使用
    const data = await res.json();
    if (!data?.current?.temperature) {
        return { content: [{ type: 'text', text: 'Upstream returned no temperature data for this city.' }], isError: true };
    }
    return { content: [{ type: 'text', text: `${city}: ${data.current.temperature}°C` }] };
});
```

检查单：

- [ ] 密钥只从 `process.env` / `os.environ` 读；README 说明需要哪些变量；宿主注册时写入配置 `env` 字段
- [ ] 所有出网请求有超时（30s 量级）与重试上限（≤2 次退避）
- [ ] 429 处理：读 `Retry-After`；持续限流时返回 `isError` 文本告知"限流中，稍后重试"
- [ ] 上游 4xx 优先翻译成模型可修正的 `isError` 文本（404/参数类）；5xx/网络故障重试后上抛
- [ ] 解析上游响应前校验关键字段；结构突变时报带上下文的错误而不是 KeyError/undefined
- [ ] 日志记录上游 URL（脱敏 query 中的密钥）、状态码、耗时

## 6. 文件与数据库访问模式

- **文件系统**：把可访问范围限定在显式根目录内；模板变量做 `realpath` + 前缀校验（完整代码见 TS 文档 §5）。返回相对路径给模型时说明基准目录。
- **数据库**：只读需求用独立只读账号/连接串；写操作工具拆分且标注 destructiveHint；查询结果分页（`limit` + `offset`/cursor），默认 `limit` 不超过 50 行；用户提供的"查询语句"类输入若必须接受，明确 SQL 注入面并优先参数化。
- **大文件**：不把整个文件塞进返回值 —— 提供按行/按范围读取的工具组合（`read_lines(path, start, end)`），description 里写清楚协作用法。

## 7. 反模式清单

| 反模式 | 后果 | 修正 |
|--------|------|------|
| description 一句话且无字段说明 | 模型不选/误选工具 | 按 §1 四问重写 |
| 万能工具（`do_action(type, payload)`） | 模型拼参数错误率极高 | 按意图拆成具体工具 |
| 返回原始 JSON 大对象 | 模型读取慢、易错 | 文本摘要 + 结构化通道 |
| `return "Error: ..."` | 模型当成功结果 | `ToolError` / `isError: true` |
| 无超时无重试的裸 fetch | 上游抖动直接失败 | §5 模板 |
| 工具数 >20 且功能重叠 | tools/list 膨胀、选择命中率跌 | 合并同类 + 参数区分，或拆服务器 |
| 破坏性工具无标注无确认 | 宿主放行造成损失 | annotations + dry_run |
| 硬编码路径/密钥 | 换环境即坏、泄密 | 环境变量 + 配置 env 字段 |
