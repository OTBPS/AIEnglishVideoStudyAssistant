# VideoTutor 视频英语学习助手

在 YouTube / Bilibili 上看英文视频学英语。观看前预习生词、带着问题看；观看中跟练字幕、标记没听清的句子；观看后做测验和听写，并按间隔重复安排复习。所有学习记录都保存在本机。

> 当前状态：**M0 骨架**，包括桌面托盘程序、本地服务、浏览器扩展连接、安装与升级流程。学习功能从 M1 开始开发。

## 安装（普通用户）

1. 从 [Releases](https://github.com/OTBPS/AIEnglishVideoStudyAssistant/releases) 下载 `VideoTutor-Setup-x.y.z.exe`，双击安装，不需要管理员权限。
   Windows 可能会提示「已保护你的电脑」，点 **更多信息 → 仍要运行**（安装包暂未签名）。
2. 安装完成后程序会自动打开向导页，按提示把浏览器扩展加载到 Chrome 或 Edge。
3. 卸载：在「设置 → 应用」里卸载即可，卸载时会询问是否同时删除学习数据。

## 开发

需要 Windows、Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js（只用于检查扩展 JS 语法）；打包还需要 Inno Setup 6。

```bash
cd backend && uv sync && cd ..                # 按 uv.lock 安装依赖
backend/.venv/Scripts/python -m videotutor --no-tray   # 从源码运行
backend/.venv/Scripts/python tools/check.py   # 全部质量门禁
backend/.venv/Scripts/python tools/build.py   # 构建 exe 和安装包
```

- 架构、模块边界、扩展方式和兼容策略见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 产品名、版本号等只能在 [product.toml](product.toml) 中修改；改完后运行 `tools/sync_product.py`。

## 许可

[Apache-2.0](LICENSE)
