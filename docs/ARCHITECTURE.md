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
app/                用例、流程状态模型、端口（Protocol）       → 调用 core（M1 起还有 domain）
   │
core/               只依赖标准库：产品信息、状态机、任务管理器
```

规则由 `import-linter` 强制（`backend/pyproject.toml` 的 `[tool.importlinter]`），CI 中违反即失败：

- 上层可以依赖下层，反之不行；
- `ui` 不能直接导入 `infra`，所有外部能力都通过 `app/ports.py` 的接口注入；
- `core` 不能导入任何第三方包。

M1 引入 `domain/`（视频、字幕、卡片、学习会话），位置在 `app` 和 `core` 之间，只能依赖 `core`。

## 模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `core/product.py` | 读取并校验 `product.toml` | 不读环境、不写文件 |
| `core/statemachine.py` | 通用状态机：显式转移表，非法转移立即抛错 | — |
| `core/tasks.py` | `TaskSupervisor`：所有后台任务的唯一入口，提供超时、取消、异常捕获和有界退出 | 不知道任务的业务含义 |
| `app/lifecycle.py` | 应用状态 CREATED→STARTING→RUNNING→STOPPING→STOPPED（以及 FAILED）；停止钩子逆序执行 | — |
| `app/settings.py` | 配置模型（严格校验 + 默认值）、`SCHEMA_VERSION`、迁移表 | 不碰文件 |
| `app/setup.py` | 首次运行向导和托盘需要的用例 | — |
| `app/ports.py` | `Locations`、`Desktop` 等接口 | 不放实现 |
| `infra/settings_store.py` | 配置的读写：迁移、损坏隔离、单字段回退、新版本文件只读、原子写 | — |
| `infra/paths.py` | 源码运行与安装后两种情况下的目录布局 | 不写死盘符 |
| `infra/http_server.py` | 预绑定端口的 uvicorn 线程 | 不含路由 |
| `infra/single_instance.py` | 命名互斥体 + `runtime.json` | — |
| `ui/http/api.py` | 路由：解析请求 → 调用 app 服务 → 组织响应 | 不含业务逻辑 |
| `ui/http/security.py` | Host/Origin 校验（防 CSRF 和 DNS 重绑定） | — |
| `ui/tray.py` | 托盘菜单，每个菜单项调用 bootstrap 注入的动作 | — |
| `extension/` | 识别视频页、侧边栏 UI；所有数据和逻辑都在桌面程序 | 不存学习数据 |

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
