# Python SDK v2（`mcp` 包）API 详解

> 核验于 2026-09-25，对应 `mcp` 2.2.0、MCP 规范 2026-07-28。
> 来源：[PyPI: mcp](https://pypi.org/project/mcp/) · [官方文档](https://py.sdk.modelcontextprotocol.io/) · [GitHub 仓库](https://github.com/modelcontextprotocol/python-sdk) · [v1→v2 迁移指南](https://py.sdk.modelcontextprotocol.io/migration/)
> 写代码前通读本文；与本文冲突的旧记忆一律以本文 + 官方文档为准。

## 1. 安装与要求

- Python **≥ 3.10**
- 项目内安装：`uv add "mcp[cli]"` 或 `pip install "mcp[cli]"`
- `cli` extra 提供 `mcp dev` / `mcp run` / `mcp install` / `mcp version` 命令行工具（`mcp dev` 需要 `npx`，因为 Inspector 是 Node 应用）
- 一次性运行（无项目）：`uv run --with "mcp[cli]" mcp run server.py`

## 2. 服务器骨架

```python
from mcp.server import MCPServer

mcp = MCPServer("Bookshop")   # 模块级对象名必须是 mcp / server / app 之一，
                              # mcp run 靠名字发现它；其他名字要显式 mcp run server.py:obj

@mcp.tool()
def search_books(query: str, limit: int = 10) -> str:
    """Search the catalog by title or author."""
    return f"Found books matching {query!r} (up to {limit})."

if __name__ == "__main__":
    mcp.run()                 # 默认 stdio；同步阻塞直到进程结束
```

三条铁律：

1. **`run()` 必须放在 `if __name__ == "__main__":` 下** —— `mcp dev`/`mcp run`/`mcp install`/测试都会 **import** 这个文件，无守卫的 `run()` 会在 import 时直接起服务器。
2. **transport 参数传给 `run()`，不是构造函数** —— `MCPServer(port=...)` 直接 `TypeError`。构造函数描述服务器"是什么"（name/instructions/log_level/debug），`run()` 描述"怎么服务"。
3. 模块级对象命名 `mcp`（或 `server`/`app`），`mcp run server.py` 才能自动发现。

构造函数可用参数：`MCPServer(name, instructions=..., log_level="INFO", debug=False)`。`log_level` 传给 `logging.basicConfig`（配置根 logger），读完落在 `mcp.settings`。

## 3. Tools（模型调用）

```python
from pydantic import Field

@mcp.tool()
def get_issue(
    repo: str = Field(description="owner/repo 形式的仓库标识"),
    limit: int = Field(default=10, description="返回条数上限", le=50),
) -> str:
    """List open issues of a GitHub repository.

    返回编号、标题、作者与更新时间的列表。用于查看仓库动态。
    """
    ...
```

- **函数名** = 工具名；**docstring** = 模型看到的 description（首行 + 详细段都会给到模型）；**类型注解** = 输入 JSON Schema。
- 有默认值的参数 = 可选参数；无默认值 = required。
- `Field(description=..., le=..., ge=...)`（Pydantic）给字段加说明与约束，SDK 校验在 handler 执行**之前**完成，非法参数到不了你的函数 —— 不要重复校验自己的类型注解。
- `async def` 完全支持；`def` 同步函数会自动放到 worker 线程执行，不会阻塞事件循环。

### 结构化输出

**返回类型注解就是输出 schema**：

```python
@mcp.tool()
def get_temperature(city: str) -> int:
    """Get current temperature in Celsius."""
    return 17
```

调用结果双通道：

```python
result.content             # [TextContent(text="17")]        ← 给模型读
result.structured_content  # {"result": 17}                  ← 给宿主程序读
```

- 标量（str/int/float/bool/bytes/None）会被包成 `{"result": ...}`；返回 Pydantic 模型或 dict 则按原结构输出。
- 声明返回 `str` 时同样产生 `{"result": "..."}` 结构化结果。
- 出错的调用 `structured_content` 为 `None`（没有可结构化的返回值）。

### Context 对象

handler 可声明 `ctx: Context` 参数获得请求级上下文：日志（`ctx.info/debug/error`）、进度上报（`ctx.report_progress`）、读取资源、采样与 elicitation 等。详见 [官方 Inside your handler 文档](https://py.sdk.modelcontextprotocol.io/handlers/)。

## 4. 错误处理（双轨模型）

```python
from mcp import MCPError
from mcp.types import INVALID_PARAMS          # 错误码常量在 mcp.types
from mcp.server.mcpserver.exceptions import ToolError, ResourceError, ResourceNotFoundError
```

| 抛什么 | 谁看到 | 效果 |
|--------|--------|------|
| `ToolError("消息")` | **模型** | 请求成功返回，`result.is_error == True`，消息进 `content`；模型读到后可自行修正重试。日志仅 INFO 一行，无堆栈 |
| `MCPError(code, message, data?)` | **宿主程序** | 整个 `tools/call` 变成 JSON-RPC 错误（如 `-32602`）；模型什么都看不到 |
| 其他任何异常 | 模型 + 你的日志 | `is_error=True` 但内容只有 `Error executing tool <name>`；完整堆栈以 ERROR 级进服务器日志 |

**判断口诀：换个更聪明的模型能不能避免这个失败？能 → `ToolError`；不能 → `MCPError`。**

- `ToolError` 消息要含修复建议：`raise ToolError(f"No book titled {title!r}. Try search_books() first.")`
- 资源未找到：`raise ResourceNotFoundError(uri)` → 协议码 `-32602`，`data` 带 URI；非"未找到"的资源失败用 `ResourceError`（`-32603` + 你的消息）。
- 禁止 `return "错误..."` —— `is_error=False`，模型把错误文案当正常结果。

## 5. Resources（宿主读取）

```python
# 静态资源
@mcp.resource("config://app")
def get_config() -> str:
    """Application configuration data."""
    return "log_level=info"

# URI 模板资源
@mcp.resource("books://{title}")
def get_book(title: str) -> str:
    """One book by exact title."""
    if title not in CATALOG:
        raise ResourceNotFoundError(f"books://{title}")
    return CATALOG[title]
```

- 模板变量即函数参数；模板匹配任意标题 ≠ 书存在，存在性由你的函数判断（找不到抛 `ResourceNotFoundError`）。
- 返回 bytes 得到二进制资源；返回 str 得到文本资源。
- 注册/注销资源自动发 `notifications/resources/list_changed`。

## 6. Prompts（用户选择）

```python
@mcp.prompt()
def review_code(code: str) -> str:
    """Review a piece of code."""
    return f"Please review this code:\n\n{code}"
```

- 函数名/参数名/docstring 的读取规则与 tool 相同；返回的 str 渲染为**一条 user 消息**。
- Prompt 参数是**扁平的命名字符串**（用户填表），没有 JSON Schema —— 不要放复杂对象参数。

## 7. 运行与传输

```python
mcp.run()                                        # stdio（默认）
mcp.run(transport="streamable-http", port=3001)  # HTTP，端点 http://127.0.0.1:3001/mcp
```

`run()` 的 transport 专属参数：`host`/`port`（默认 127.0.0.1:8000）、`streamable_http_path`（默认 `/mcp`）、`json_response=True`（单 JSON 响应，无 SSE 流；会禁用进行中请求的通知）、`stateless_http=True`（每请求新 transport，无会话跟踪）、`max_request_body_size`（默认 4 MiB，超限 413）。

CLI：

```bash
uv run mcp dev server.py                    # 起 Inspector 调试（需 npx）
uv run mcp dev server.py --with pandas      # 临时加依赖
uv run mcp run server.py                    # 直接运行（找 mcp/server/app 对象调 run()）
uv run mcp run server.py:bookshop           # 对象名非默认时显式指定
uv run mcp install server.py --name "Bookshop" -v API_KEY=xxx -f .env   # 注册到 Claude Desktop
```

注意：`mcp run` 只转发 `--transport`，其余 run() 参数写死在代码里；`mcp dev`/`mcp run` 只认 `MCPServer`（低层 `Server` 要自己跑）。

## 8. 测试（内存 Client，无端口无子进程）

开发依赖：`uv add --dev pytest anyio`（可选 `inline-snapshot`）。

```python
# test_server.py
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from server import mcp            # 导入即拿到服务器对象

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c

@pytest.mark.anyio
async def test_add(client: Client):
    result = await client.call_tool("add", {"a": 1, "b": 2})
    assert result.content[0].text == "3"
    assert result.structured_content == {"result": 3}

@pytest.mark.anyio
async def test_tool_error(client: Client):
    result = await client.call_tool("get_book", {"title": "Nothing"})
    assert result.is_error is True
    assert "No book" in result.content[0].text
```

- `raise_exceptions=True` 只影响**工具体之外**的失败（连接级）：测试里开它以看到真实错误而非脱敏后的 `Internal server error`；工具内部的 `ToolError` 无论开关都返回 `is_error=True` 结果 —— 断言结果，不要指望 catch。
- `Client(mcp)` 内存连接默认 era-neutral（自动探测协议版本）。

## 9. 部署相关

- Streamable HTTP 默认绑定 127.0.0.1；公网部署要处理 `transport_security`（DNS 重绑定防护）、鉴权与多 worker，见 [Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/) 与 [Authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/)。
- 挂进既有 ASGI 应用（FastAPI 等）不用 `run()`，自己构建 ASGI app，见 [Add to an existing app](https://py.sdk.modelcontextprotocol.io/run/asgi/)。
- `MCP_*` 环境变量与 `.env` 自动加载在 v2 已移除 —— 需要的配置显式读取 `os.environ`。
- Claude Desktop 每服务器日志：macOS `~/Library/Logs/Claude/mcp-server-<NAME>.log`；Windows `%APPDATA%\Claude\logs`。
