# VideoTutor 架构

模块化单体：一个进程（托盘程序 + 本地 HTTP 服务）+ 一个浏览器扩展。不拆微服务——单用户桌面软件没有独立伸缩或独立部署的需求，拆分只会增加部署和调试成本。

## 分层与依赖方向

```
bootstrap.py        组合根：唯一知道所有具体类的地方，负责装配与启动顺序
   │
ui/                 交付层：HTTP 路由、托盘、静态网页         → 只调用 app
   │
infra/              适配器：文件、配置存储、日志、互斥体、     → 实现 app 定义的端口
   │                OS 外壳、uvicorn
app/                用例、流程状态模型、端口（Protocol）       → 调用 domain、core
   │
domain/             纯业务模型：视频、字幕、观看会话           → 只调用 core；禁止框架和 I/O 库
   │
core/               只依赖标准库：产品信息、状态机、任务管理器
```

规则由 `import-linter` 强制（`backend/pyproject.toml` 的 `[tool.importlinter]`），CI 中违反即失败：

- 上层可以依赖下层，反之不行；
- `ui` 不能直接导入 `infra`，所有外部能力都通过 `app/ports.py` 的接口注入；
- `core` 不能导入任何第三方包；
- `domain` 不能导入 fastapi、pydantic、sqlite3、urllib 等框架和 I/O 库。

## 模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `core/product.py` | 读取并校验 `product.toml` | 不读环境、不写文件 |
| `core/statemachine.py` | 通用状态机：显式转移表，非法转移立即抛错 | — |
| `core/tasks.py` | `TaskSupervisor`：所有后台任务的唯一入口，提供超时、取消、异常捕获和有界退出 | 不知道任务的业务含义 |
| `app/lifecycle.py` | 应用状态 CREATED→STARTING→RUNNING→STOPPING→STOPPED（以及 FAILED）；停止钩子逆序执行 | — |
| `app/settings.py` | 配置模型（严格校验 + 默认值）、`SCHEMA_VERSION`、迁移表 | 不碰文件 |
| `app/setup.py` | 首次运行向导和托盘需要的用例 | — |
| `app/ports.py` | `Locations`、`Desktop`、各仓储与 `CaptionDecoder` 接口 | 不放实现 |
| `app/videos.py` | 观看用例：打开视频并决定要采集哪条字幕、保存字幕、记录观看事件、关闭会话、最近观看 | 不知道字幕格式和数据库 |
| `domain/video.py` | `VideoRef`（按平台校验 ID）、`CaptionTrack`、选轨规则（人工 > 自动，en > en-XX） | — |
| `domain/transcript.py` | `Transcript`/`Segment` 不变式；`normalize` 清洗、合并滚动重复、消除重叠 | — |
| `domain/watch.py` | `WatchSession` 状态机；只计入真实前进播放的时长，乱序或重复事件忽略 | — |
| `domain/vocab.py` | 分词（跳过声音标签、拆缩写）、按整段判定人名、掌握判定、覆盖率与难度分档、预习候选 | 读词表文件（交给 `infra/lexicon.py`） |
| `domain/prewatch.py` | 释义、问题、预测提示的不变式 | — |
| `app/vocabulary.py` | 某视频对当前学习者的词汇画像（每段字幕的词、人名、覆盖率）；标记词状态 | — |
| `app/prewatch.py` | 课前预习：一次 AI 调用生成释义和问题，作为后台任务运行，结果入库；单词点查 | 知道用的是哪家 AI |
| `app/llm_settings.py` | AI 服务配置：保存模型和密钥、连通测试 | 保存密钥明文 |
| `infra/lexicon.py` | 读取内置英文词表（`tools/build_lexicon.py` 生成） | — |
| `infra/llm/` | `ClaudeLanguageModel`（官方 SDK、结构化输出、拒答回退）、`OpenAICompatibleLanguageModel`、按配置构建的 provider | 提示词和业务校验 |
| `infra/secrets.py` | API 密钥存 Windows 凭据管理器 | 写文件或日志 |
| `infra/db/` | SQLite：打开时新建、损坏隔离、备份后迁移、新版本只读；单连接加锁；仓储实现 | 业务规则 |
| `infra/captions/youtube.py` | YouTube json3 / srv3 解码 | 选轨、清洗（属于 domain） |
| `infra/settings_store.py` | 配置的读写：迁移、损坏隔离、单字段回退、新版本文件只读、原子写 | — |
| `infra/paths.py` | 源码运行与安装后两种情况下的目录布局 | 不写死盘符 |
| `infra/http_server.py` | 预绑定端口的 uvicorn 线程 | 不含路由 |
| `infra/single_instance.py` | 命名互斥体 + `runtime.json` | — |
| `ui/http/api.py` | 组装应用：安全中间件、路由、错误映射（404/409/422）、静态页面 | 不含业务逻辑 |
| `ui/http/routes/*.py` | 各区域的薄路由：解析请求 → 调用 app 服务 → 组织响应 | 不含业务逻辑 |
| `ui/http/schemas.py` | 线上格式（pydantic）与大小上限，和 domain 数据类分开 | — |
| `ui/http/security.py` | Host/Origin 校验（防 CSRF 和 DNS 重绑定） | — |
| `ui/tray.py` | 托盘菜单，每个菜单项调用 bootstrap 注入的动作 | — |
| `extension/src/youtube/page.js` | 运行在页面主环境：读取播放器的视频信息，转发播放器自己的字幕响应（需要只有播放器才能生成的 pot 令牌，扩展无法自行请求） | 解析字幕 |
| `extension/src/youtube/content.js` | 每个标签页的观看状态、播放事件、与侧边栏的长连接 | 访问桌面程序（交给 service worker） |
| `extension/src/background.js` | 唯一调用桌面程序 API 的地方；关闭标签页时结束会话；升级后自动重新加载扩展 | — |
| `extension/src/sidepanel.js` | 跟随当前标签页显示字幕，高亮当前句，点击跳转 | 存学习数据 |

## 关键机制

**唯一来源。** 版本号、产品名、安装器 AppId、端口范围、API 协议号和扩展公钥只写在 `product.toml` 一处：
- Python 在运行时读取它；
- 扩展的 `manifest.json` 和 `src/product.js` 由 `tools/sync_product.py` 生成；
- 安装器和 exe 版本资源在构建时由 `tools/build.py` 和 spec 注入；
- 依赖只记录在 `backend/pyproject.toml` 和 `uv.lock`，CI 用 `uv sync --frozen` 安装。

**后台任务。** 一律通过 `TaskSupervisor.submit()`（协程）或 `run_blocking()`（阻塞调用，在守护线程中运行）提交：
- 默认超时 120 秒，需要不限时必须显式传 `timeout=None`；
- 异常会写进任务状态并记录日志，不会拖垮事件循环；
- 应用退出时取消所有任务，最多等待一个宽限期。卡住的阻塞调用会被放弃，而不是一直等它结束。

**状态模型。** 重要流程都用 `StateMachine` 加枚举表示，不用零散的布尔变量。目前有两个：应用生命周期（`AppState`）和任务状态（`TaskState`）；M1 会加上学习会话（课前、课中、课后）。

**AI 调用。** 业务层只依赖 `LanguageModel` 接口（一次结构化 JSON 请求），提示词、输出 schema 和校验都在 `app/`；
具体调哪家 AI 是 `infra/llm/` 的事，换模型或换服务商不改业务代码。默认用 Claude 官方 SDK（`claude-opus-5-5`，
`effort: low`，JSON schema 结构化输出，开启服务端拒答回退）；给无法访问 Claude 的地区保留兼容 OpenAI 接口的实现。
所有 AI 调用都在 `TaskSupervisor` 里运行（有超时，退出时取消）；结果入库，同一视频不重复计费；自动触发的生成失败后不会自动重试。
密钥只存在操作系统凭据管理器里，接口只能写入、不能读出。

**词汇。** 词表在构建时从 wordfreq + simplemma 抽取英文部分（236 KB，许可见 `THIRD_PARTY_NOTICES.md`），
运行时只用标准库。分词只在 Python 里做，接口把每段字幕的词位置（span）发给侧边栏，前端不重复实现分词。

**测试/演示实例隔离。** 设置了 `VIDEOTUTOR_HOME` 的运行（测试、演示、便携版）使用按数据目录区分的单实例锁和凭据管理器名称，
既不会被正在运行的正式程序挡住，也绝不会读到或覆盖正式安装的 API 密钥。

**本地 API 安全。**
- 只监听 127.0.0.1；
- Host 头必须是本机地址加本端口；
- 写操作只接受来自本程序页面或本扩展的 Origin。扩展 ID 由 `product.toml` 里的公钥固定，在每台电脑上都一样。

## 如何扩展

| 要加的东西 | 做法 |
|---|---|
| 新功能（如「生成生词卡」） | 在 `app/` 写用例服务；外部能力（LLM、字幕源）先在 `app/ports.py` 定义 Protocol，再在 `infra/` 实现，然后在 `bootstrap.py` 装配，最后在 `ui/http/api.py` 加一条薄路由 |
| 耗时操作 | 在用例里调用 `tasks.submit(...)`，路由返回任务 id 或等待它的 future；不要自己 `threading.Thread` |
| 配置字段 | 在 `Settings` 里加带默认值的字段即可，不需要迁移 |
| 改名、删除或改变配置字段含义 | `SCHEMA_VERSION += 1`，并在 `MIGRATIONS[旧版本]` 加纯函数；已发布的迁移永远不要改 |
| 可替换的实现（如换 ASR 后端） | 新增一个实现同一 Protocol 的 adapter，在 bootstrap 里按条件选择 |
| 新状态流程 | 定义枚举和转移表，交给 `StateMachine` |

只有在出现真实的第二个实现，或测试真的需要替身时才加接口。不要预先抽象。

## 兼容性策略

| 对象 | 规则 |
|---|---|
| 学习数据目录 `%LOCALAPPDATA%\VideoTutor` | 路径永不改变；升级和卸载默认都不删除 |
| `settings.json` | 带 `schema_version`，旧版本文件自动迁移并保留 `.vN.bak` 备份；文件比程序新时只读，不覆盖 |
| SQLite（M1 起） | 与配置同样的规则：版本表、逐步迁移、迁移前备份、遇到更新版本的数据库拒绝写入 |
| 扩展 ↔ 程序 API | `/api/health` 返回 `protocol`。破坏性改动时 `protocol += 1`，且新程序至少保留一个版本的旧行为；扩展发现不兼容时提示用户更新 |
| 安装器 `app_id`、扩展公钥 | 永不改变：改 AppId 会让升级找不到旧安装，改公钥会让所有人的扩展失效 |
| 安装版本 | 禁止直接降级（可用 `/ALLOWDOWNGRADE` 覆盖）；增量包只能装在它对应的基线版本上 |

必须破坏兼容时，需要在 PR 说明里写明：影响谁、自动迁移怎么做、用户需要做什么、怎么回滚。

## 构建与发布

```bash
python tools/check.py            # 质量门禁：ruff、格式、mypy --strict、分层规则、pytest 覆盖率 ≥85%、扩展 JS 语法
python tools/build.py            # 门禁 → exe → 打包后自检 → 完整安装包 → 发布清单
python tools/verify_install.py   # 真实安装、修复、降级拦截、增量包基线校验、卸载
```

发布流程：修改 `product.toml` 的 `version` → 运行 `python tools/sync_product.py` → 提交 → 打 `vX.Y.Z` 标签并推送。之后 CI 会自动完成以下步骤：
1. 校验标签与版本号一致；
2. 下载上一个发布的 `*-files.json` 并生成增量包；
3. 运行安装验证（包括「旧版 → 增量 → 文件与新版一致」）；
4. 发布到 GitHub Releases，产物包括完整包、增量包、`*-files.json`、`release-manifest.json` 和 `SHA256SUMS.txt`。

## 暂不做的事

- 依赖注入容器、插件系统、事件总线：在 bootstrap 里显式传构造参数已经足够。
- 应用内自动下载并安装更新：发布清单已经包含所需信息，客户端检查更新放到后续里程碑。
- 代码签名、国际化框架、网页前端构建链（侧边栏需要组件化时再引入 Vite）。
