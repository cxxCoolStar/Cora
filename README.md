# Cora

`Cora` 当前是一套以 `ClawBot` 为核心的“资料归档 + 检索 + 跟进”运行时。

它不是单纯的聊天壳，而是把文本、链接、文件和图片沉淀到本地知识库，再通过 LLM + tool calling 完成保存、检索、主题归类、全文展开、摘要和文件回传。

## 当前能力

- 保存文本、链接、普通文件和图片到本地资料库
- 为资料自动生成摘要、标签、定位提示和 topic 归类
- 支持按 topic 检索、按文件名或上下文继续追问、展开全文、生成摘要
- 对同一文档做版本识别，默认只展示当前版本
- 提供本地 FastAPI 调试 UI 和调试页
- 提供微信 iLink 轮询链路，支持把微信消息/文件接入同一套会话
- 在微信链路已配置时，支持把原始文件回发给用户

## 代码里的主入口

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

### 本地 Web 调试

```powershell
python -m core.cli.main serve
```

默认地址：

- 聊天页: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- 调试页: [http://127.0.0.1:8000/debug](http://127.0.0.1:8000/debug)

### 微信登录

```powershell
python -m core.cli.main wechat-login
```

登录成功后，账号配置会保存在：

```text
.cora/wechat/accounts/
```

### 微信轮询

```powershell
python -m core.cli.main wechat-poll
```

轮询链路会：

- 复用同一微信用户的会话
- 对重复事件做去重
- 把微信文本/文件转成统一的 `ingest` 流程
- 在能力可用时支持把原始文件再发回微信用户

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

### 页面路由

- `GET /`
- `GET /debug`

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
3. 执行 `python -m core.cli.main serve`
4. 打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)
5. 先测试文本保存、文件上传、topic 检索
6. 再视需要接入微信登录和轮询
