# Cora

`Cora` 是我为微信文件传输助手这个真实使用场景写的一个 agent，也是个人 **agent 开发练手项目**（长期目标与阶段对照见 [docs/project-goals.md](docs/project-goals.md)）。

我在长期使用微信文件传输助手时，反复遇到两个痛点：

1. 很久之前存过一个文件，后来只记得内容，不记得文件名，真要找的时候得在聊天记录里翻很久。
2. 所有文件都只是“发过去存一下”，没有分类、没有整理，时间一长不仅难找，以后做数据迁移也很麻烦。

`Cora` 就是为了解决这两个问题而写的。

你可以把文件直接发给它，它会帮你自动整理、归档和分类；下次需要时，不用再记文件名，可以直接跟它对话，例如“把之前那个简历发给我”或者“帮我找一下之前保存的网络配置”，它会尽量定位到目标资料，并在微信链路可用时直接把原文件再发回给你。

从实现上看，它不是单纯的聊天壳，而是把文本、链接、文件和图片沉淀到本地知识库，再通过 LLM + tool calling 完成保存、检索、主题归类、全文展开、摘要和文件回传。

## 一句话理解

`Cora` 想做的事情很简单：
把“微信文件传输助手里的文件堆”变成一个可以对话、可以整理、可以回传原文件的小型个人知识库。

## 你可以期待它做什么

- 发文件给它，它替你存档、分类、打标签
- 只记得内容、不记得文件名时，直接用自然语言找文件
- 想把原文件拿回来时，直接让它发回微信
- 把原本散落在聊天记录里的文件，慢慢整理成可迁移的数据资产

## 这个项目解决什么问题

- 让“只记得内容、不记得文件名”的资料也能通过对话找回来
- 让微信文件传输助手里的零散文件，逐步变成可整理、可检索、可迁移的知识库
- 让文件不仅是“存起来”，还能被自动分类、打标签、归到 topic
- 让后续取回文件时，可以直接让 agent 帮你发回来，而不是手动翻聊天记录

## 当前能力

- 保存文本、链接、普通文件和图片到本地资料库
- 为资料自动生成摘要、标签、定位提示和 topic 归类
- 支持按 topic 检索、按文件名或上下文继续追问、展开全文、生成摘要
- 对同一文档做版本识别，默认只展示当前版本
- 提供微信 iLink 轮询链路，支持把微信消息/文件接入同一套会话
- 在微信链路已配置时，支持把原始文件回发给用户
- 对高风险工具支持人工确认（HITL）：微信里回复「确认」或「拒绝」后再执行（见 [docs/wechat-hitl.md](docs/wechat-hitl.md)）

## 一个典型用法

1. 你把一个文件发给 `Cora`
2. `Cora` 自动保存原文件、提取文本、生成摘要、打标签并尝试归类
3. 过了一段时间，你只记得“那是之前发过的一份简历”或“一个网络配置文档”
4. 你直接跟它说“把之前那个简历发给我”或“帮我找一下内网配置”
5. `Cora` 会根据已保存的内容和上下文做检索，并在可用时直接把原文件再发回微信

更贴近真实使用的话，大概就是这样：

```text
你：帮我找一下之前发过的那份简历
Cora：我找到了两份相关资料，你说的是第一份还是第二份？
你：第一份，直接发给我
Cora：好，已经把原文件发回给你了。
```

## 项目结构

- Web / 本地调试入口: [src/core/api/app.py](src/core/api/app.py)
- CLI 入口: [src/core/cli/main.py](src/core/cli/main.py)
- 依赖装配: [src/core/clawbot/dependencies.py](src/core/clawbot/dependencies.py)
- 会话主服务: [src/core/clawbot/service.py](src/core/clawbot/service.py)
- 归档工具执行器: [src/core/clawbot/tools.py](src/core/clawbot/tools.py)
- 文件摄取: [src/core/ingestion/service.py](src/core/ingestion/service.py)
- 微信网关: [src/core/channels/wechat/service.py](src/core/channels/wechat/service.py)

## 运行要求

- Python 3.11+
- Windows、macOS、Linux 均可运行
- 如果要解析 `.doc`，Windows 下最好安装 Microsoft Word
- 如果要启用真实模型，当前代码要求配置 OpenAI 兼容的 Chat Completions 接口

## 安装

### Windows PowerShell

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## 配置

配置由 [src/core/config.py](src/core/config.py) 中的 `CoreSettings` 负责加载，默认从项目根目录 `.env` 读取，环境变量统一使用 `CORA_` 前缀。

### 最小可运行配置

如果你要接真实模型，至少需要：

```env
CORA_MODEL_PROVIDER=openai
CORA_MODEL=gpt-4.1-mini
CORA_OPENAI_API_KEY=your_api_key
CORA_OPENAI_BASE_URL=https://api.openai.com/v1
```

### 本地开发桩模式

如果你只是想先把服务跑起来，不接真实模型，可以启用开发桩：

```env
CORA_DEBUG=true
```

这会使用 [src/core/llm/dev_client.py](src/core/llm/dev_client.py) 里的 `DevelopmentModelClient`。它适合调接口和流程，不适合验证真实归档策略。

### 常用配置项

```env
CORA_CLAWBOT_DATABASE_PATH=.cora/clawbot.db
CORA_FILES_STORAGE_DIR=.cora/files
CORA_ARCHIVE_ROOT_DIR=.cora/archive

CORA_CONTEXT_LENGTH=128000
CORA_CONTEXT_COMPRESSION_THRESHOLD=0.50
CORA_CONTEXT_SUMMARY_TARGET_RATIO=0.20
CORA_CONTEXT_PROTECT_LAST_N_MIN=8
```

### MCP 外部工具（可选）

在 `config/mcp_servers.json` 中配置 MCP Server，并在 gateway 启动前启用：

```env
CORA_MCP_ENABLED=true
CORA_MCP_CONFIG_PATH=config/mcp_servers.json
```

MCP 工具名称格式为 `mcp_{server}_{tool}`，并受与内置工具相同的 harness policy / HITL 约束（见 `src/core/agent/policy_profiles.py`）。

### 通用归档 skill（archive-core）

`skills/archive-core` 是可移植的独立包（不依赖 Cora 业务代码），任意 agent 可通过 CLI 调用：

```powershell
pip install -e skills/archive-core
$env:ARCHIVE_ROOT = ".cora/archive"
'{"schema_version":"1.0","intent":"overview"}' | archive-cli
```

Cora 通过 `core/skills` 装配 `archive-core`（`adapters/cora/` + `archive_run` 工具）；契约见 `skills/archive-core/references/PORTABLE.md` 与 `references/cora.md`。

微信主路径默认开启镜像（`CORA_ARCHIVE_MIRROR_ENABLED=true`）：ingest 后写入 `archive_index.jsonl`，检索/回传在 DB 无结果时会查文件库。详见 `skills/archive-core/references/cora.md`。

### 图片视觉描述配置

图片上传会进入 archive 流程。若要为图片生成可检索的视觉描述，可额外配置：

```env
CORA_AUXILIARY_VISION_PROVIDER=openai
CORA_AUXILIARY_VISION_MODEL=gpt-4.1-mini
CORA_AUXILIARY_VISION_API_KEY=your_api_key
CORA_AUXILIARY_VISION_BASE_URL=https://api.openai.com/v1
CORA_AUXILIARY_VISION_TIMEOUT_SECONDS=60
```

如果不配视觉模型，图片仍然会被归档，但描述会退化为“已保存图片，分析不可用”的兜底文本。

### 微信配置

```env
CORA_WECHAT_ENABLED=true
CORA_WECHAT_BASE_URL=https://ilinkai.weixin.qq.com
CORA_WECHAT_ACCOUNT_NAME=default
CORA_WECHAT_POLL_TIMEOUT_SECONDS=35
```

如果你不想每次扫码，也可以直接提供：

```env
CORA_WECHAT_TOKEN=your_token
```

## 启动方式

微信链路是这个项目当前的主使用路径。

### 本地 Web 调试

```powershell
python -m core.cli.main serve
```

这个入口现在只适合开发时做接口联调，不是主要产品入口。

### 微信登录

```powershell
python -m core.cli.main wechat-login
```

登录成功后，账号配置会保存在：

```text
.cora/wechat/accounts/
```

然后启动统一 gateway：

```powershell
python -m core.cli.main gateway
```

### 统一 Gateway

```powershell
python -m core.cli.main gateway
```

统一 gateway 会：

- 同时运行微信轮询和定时任务 worker
- 复用同一微信用户的会话
- 对重复事件做去重
- 把微信文本/文件转成统一的 `ingest` 流程
- 在能力可用时支持把原始文件再发回微信用户

`python -m core.cli.main wechat-poll` 仍然可用，但现在只是兼容别名，会启动同一个 gateway 运行时。

## 评测

为了避免每次改完都只能“凭感觉”，项目现在支持一个最小可用的本地 eval runner。

### 测试集目录

```text
evals/cases/<type>/*.json
```

每个 case 可以定义多步对话，runner 会为每个 case 创建独立的临时 `.cora` 工作区，避免污染你平时使用的数据库和归档目录。

推荐的一级类型目录是：

- `regression`
- `capability`
- `safety`

### 运行方式

```powershell
python -m core.cli.main eval-run
```

只跑某一类 case 时可以用：

```powershell
python -m core.cli.main eval-run --case-type regression
```

默认会：

- 递归读取 `evals/cases/` 下的全部 `.json` case
- 逐个创建独立 session 执行
- 输出通过/失败摘要
- 将详细报告写入 `.cora/evals/latest.json`

### Case 示例

```json
{
  "id": "user_memory_roundtrip",
  "description": "The agent should persist and recall durable user memory.",
  "steps": [
    {
      "input": {"text": "记住：我常买的布洛芬品牌是芬必得。"},
      "expect": {"tool_names_any": ["user_memory"]}
    },
    {
      "input": {"text": "我常买的布洛芬是什么牌子？"},
      "expect": {"reply_contains_any": ["芬必得"]}
    }
  ]
}
```

当前支持的断言重点偏行为而不是文案，包括：

- `status`
- `disposition`
- `action`
- `tool_names_any`
- `tool_names_all`
- `reply_contains_all`
- `reply_contains_any`
- `reply_not_contains`
- `artifact_ref_contains_any`
- `max_trace_messages`

## 本地存储

默认会生成以下目录：

- `.cora/clawbot.db`
  SQLite 数据库，保存 session、message、item、topic、clarification 等数据
- `.cora/files`
  上传文件的落盘目录
- `.cora/archive`
  archive-core 图片归档目录

## 文件支持范围

当前代码中的文件解析逻辑来自 [src/core/ingestion/service.py](src/core/ingestion/service.py)。

### 明确支持

- 纯文本: `.txt`
- Markdown / 文档: `.md` `.markdown` `.docx` `.pdf`
- 旧版 Word: `.doc`
- 图片: `.png` `.jpg` `.jpeg` `.webp`
- 常见代码/配置文件:
  `.py` `.pyi` `.js` `.jsx` `.ts` `.tsx` `.java` `.go` `.rs` `.c` `.cc` `.cpp` `.cxx` `.h` `.hh` `.hpp` `.hxx` `.cs` `.php` `.rb` `.swift` `.kt` `.kts` `.scala` `.lua` `.sql` `.sh` `.bash` `.zsh` `.ps1` `.bat` `.cmd` `.json` `.yaml` `.yml` `.xml` `.toml` `.ini` `.cfg` `.conf` `.properties` `.env` `.gitignore` `.dockerignore` `.editorconfig` `.csv` `.tsv` `.log` `.rst` `.mdx`

### 未知后缀的兜底策略

- 先尝试按纯文本读取
- 支持 `utf-8`、`utf-8-sig`、`utf-16`、`gb18030`、`gbk` 等常见编码
- 如果文件明显像二进制，则不会强行解析，而是保留为 `file_upload`

### 图片处理说明

- 图片不会走普通文本解析器
- 图片会进入 `archive-core` 流程保存到 archive 目录
- 若配置了视觉模型，会写入可检索的视觉描述

### 文档版本识别

上传同一文档的新版本时，系统会基于文件名 stem 做文档键归一化，并：

- 生成新版本号
- 保留旧版本历史
- 默认列表只展示当前版本

## API

### 创建会话

```http
POST /sessions
```

### 发送文本或文件到归档入口

```http
POST /sessions/{session_id}/ingest
Content-Type: multipart/form-data
```

表单字段：

- `text`: 可选，文本内容
- `file`: 可选，上传文件

### 普通回复入口

```http
POST /sessions/{session_id}/reply
Content-Type: multipart/form-data
```

字段：

- `text`: 必填

### 查看当前资料列表

```http
GET /sessions/{session_id}/items
```

### 查看单条资料详情

```http
GET /sessions/{session_id}/items/{item_id}
```

## 典型交互流程

### 保存一段资料

用户发送：

```text
请保存这份内网配置：地址10.30.1.127，网关10.30.0.1
```

系统会：

1. 落库为 item
2. 生成摘要
3. 抽取标签
4. 自动归到 topic
5. 返回保存结果

### 上传一个文件

如果用户只上传文件、不带明确说明，系统通常会先追问一次，例如让用户确认“保存”还是补充备注。确认后再正式归档。

### 继续追问

在打开 topic 或列出候选结果后，用户可以继续说：

- “第一个给我全文”
- “这里面写了什么”
- “总结一下”
- “把这个文件发给我”

这些 follow-up 都会走 tool calling，而不是只靠单轮字符串匹配。

这也是 `Cora` 跟“把文件丢进微信文件传输助手”最大的区别之一：它不只是替你存文件，而是试图理解你后续要找的到底是哪一份资料。

## 为什么不是直接继续用文件传输助手

文件传输助手适合“临时发一下”，但不适合“长期积累后还能方便找回”。

当文件越来越多时，问题会越来越明显：

- 你依赖的是聊天记录顺序，而不是结构化索引
- 你依赖的是文件名记忆，而不是内容检索
- 你得到的是一堆消息，而不是一个可迁移、可整理的资料库

`Cora` 试图补上的，就是这一层“整理”和“可对话找回”的能力。

## Topic 与检索

当前 topic 能力由 [src/core/topics/service.py](src/core/topics/service.py) 驱动。

系统会：

- 在保存资料时自动分配 topic
- 在检索时优先命中 topic，再展开关联 item
- 在需要时自动补齐未建索引的 item

这意味着项目更接近“轻量本地知识库”而不是单纯消息历史。

## 与模型的关系

当前代码已经去掉了早期的纯启发式主流程，运行时必须能得到一个 `ModelClient`：

- `OpenAIChatModelClient`
- `DevelopmentModelClient`

OpenAI 适配器走的是兼容 Chat Completions 的 `tools` / `tool_choice=auto` 能力，代码在 [src/core/llm/openai_client.py](src/core/llm/openai_client.py)。

## 测试

运行全部测试：

```powershell
pytest
```

### Harness eval smoke gate

Run the production harness smoke cases locally with:

```powershell
.\scripts\run_harness_evals.ps1
```

If your local PowerShell execution policy blocks `.ps1` files, use:

```powershell
.\scripts\run_harness_evals.cmd
```

This is equivalent to:

```powershell
python -B -m core.cli.main eval-run --case-type harness --report-path .cora/evals/harness-latest.json
```

The harness smoke gate currently covers **40 cases** (run all harness-type evals), including:

- normal single-agent completion
- tool-using turns and `tool.completed` trace events
- timeout budget handling and `budget.timeout`
- expected harness failure handling and `run.failed`
- per-run allow/deny tool policy and named policy profiles
- MCP tool policy (`mcp_tool_respects_policy`) when external servers are configured
- WeChat HITL confirm flow (`wechat_hitl_confirm_command`) — see [docs/wechat-hitl.md](docs/wechat-hitl.md)

Phase 3 planning design draft: [docs/cora-phase3-planning-design.md](docs/cora-phase3-planning-design.md).

### Harness policy profiles

Harness tool permissions are configurable through environment variables:

- `CORA_HARNESS_POLICY_PROFILE`: global fallback profile, unset by default.
- `CORA_WECHAT_HARNESS_POLICY_PROFILE`: default profile for WeChat turns, `wechat_safe` by default.
- `CORA_JOB_HARNESS_POLICY_PROFILE`: default profile for job execution sessions, `background_readonly` by default.

The built-in profiles are defined in `src/core/agent/policy_profiles.py`.
The CLI TUI entry uses `coding_full` as its fallback profile unless
`CORA_HARNESS_POLICY_PROFILE` is already set.

项目里目前有覆盖以下关键路径的测试：

- 本地 API ingest / reply
- 文本、链接、文档、代码文件、图片上传
- 未知文本后缀兜底与二进制阻断
- topic 归类与检索
- 文档版本更新
- 微信 session 复用与事件去重
- 文件回传能力

## 当前限制

- 没有配置模型时，服务不会启动
- `.doc` 解析在不同平台上的稳定性依赖 Word / pandoc / docling 实际环境
- 未知二进制文件只会保留原文件，不会提取可检索正文
- 图片回传能力只有在微信网关和用户映射已配置时才会暴露

## 推荐的最小试跑流程

1. 创建虚拟环境并安装依赖
2. 配置 `.env`
3. 执行 `python -m core.cli.main wechat-login`
4. 执行 `python -m core.cli.main gateway`
5. 用微信把文件、文本或图片发给 `Cora`
6. 继续通过对话测试“帮我找一下”“把那个文件发给我”“总结一下这份资料”

如果你是第一次跑这个项目，最值得先验证的不是“它能不能存文件”，而是这三件事：

1. 发一份文件给它，它能不能自动归档
2. 过一会儿只用内容描述，它能不能把文件找出来
3. 在微信链路可用时，它能不能把原文件再发回给你
