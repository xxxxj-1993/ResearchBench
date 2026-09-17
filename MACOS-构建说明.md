# ResearchBench v2.4.4 macOS 版

## 已适配功能

| 功能 | macOS 实现 |
|---|---|
| 本地数据 | `~/Library/Application Support/ResearchWorkbench/` |
| 文件和文件夹 | 使用 macOS `open` 命令 |
| Finder 定位 | 使用 `open -R` |
| 文件选择 | 使用系统 AppleScript 选择器 |
| 软件启动 | 支持 `/Applications/*.app` 与 `~/Applications/*.app` |
| 软件探测 | 覆盖常见科研软件的 macOS `.app` 路径 |
| Zotero | 读取 macOS 默认配置及 `~/Zotero` |
| Obsidian | 读取 macOS 默认配置目录 |
| 界面窗口 | 使用 Cocoa 原生 WebView，初始化失败时调用本机浏览器 |

## 在 Mac 上一键构建

需要 macOS 11 或更高版本、Python 3.11+。构建脚本会安装
`requirements-macos.txt` 中声明的 PyInstaller 与 pywebview：

```bash
chmod +x macos/build_macos.sh
./macos/build_macos.sh
```

构建结果：

- `dist/ResearchBench-v2.4.4.app`
- `dist/ResearchBench-v2.4.4-macOS.dmg`

## 使用 GitHub Actions 构建

将 `repo` 内容推送到 GitHub 仓库，进入仓库的 **Actions** 页面，选择
**Build macOS package**，点击 **Run workflow**。完成后从 Artifacts 下载 DMG。

## 首次打开

当前默认构建没有 Apple Developer 签名和公证。首次运行时，如果 macOS 阻止打开：

1. 在 Finder 中找到应用；
2. 按住 Control 点击应用，选择“打开”；
3. 再次点击“打开”。

正式公开分发时建议配置 Apple Developer ID 签名与 notarization 公证。

## 架构说明

构建产物采用构建机本身的处理器架构。若需要同时发布 Intel 与 Apple Silicon
两个版本，应分别在对应架构的 Mac/GitHub runner 上构建；PyInstaller 不能在
Windows 上交叉编译 macOS 应用，也不能凭空把单架构 Python 变成通用包。
