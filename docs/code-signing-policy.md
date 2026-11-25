# Code signing policy

**The installers are currently not code-signed.** Windows SmartScreen and macOS Gatekeeper will warn on first launch; see "First launch" below.

This policy covers the installers of the desktop app **初星助手** (Hatsuboshi Assistant, `app/` in this repository) published on the
[GitHub Releases](https://github.com/matto49/hiro-gakumas-hero/releases) page under `desktop-v*` tags.

## What would be signed

If the project gets a code signing certificate later, this is what it will cover:


- Only files built by the [`desktop` GitHub Actions workflow](../.github/workflows/desktop.yml) from the source code in this repository:
  the Windows installer `chu-xing-zhu-shou-setup-<version>-amd64.exe` and the app executable inside it.
- The installer also contains [uv](https://github.com/astral-sh/uv) (Apache-2.0 / MIT), downloaded unmodified from its official releases by
  `app/scripts/fetch-uv.mjs`. It is an upstream binary and is not signed with this project's certificate.
- Every signing request is approved manually by an approver (below) after checking that the build comes from a commit on `main`.

## Team roles

The project is maintained by one person.

- Committers and reviewers: [matto49](https://github.com/matto49) (repository owner). Pull requests from anyone else are reviewed by the owner before merging.
- Approvers: [matto49](https://github.com/matto49).

All team members use multi-factor authentication for GitHub.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.

What the app does over the network, all started by the user:

- On first setup it downloads this repository from GitHub, a Python runtime and Python packages (from PyPI, or the mirrors
  `pypi.tuna.tsinghua.edu.cn` and `registry.npmmirror.com` when PyPI is slow), and public game data and models from GitHub releases.
- While playing, it sends the current game state (text and screenshots of the game) to the large language model provider the user
  configures (for example DeepSeek), using the user's own API key. Nothing is sent when no provider is configured.
- It reads public strategy data from [gakumas.wiki](https://gakumas.wiki/). It does not upload anything there.

The app does not collect telemetry. API keys stay on the user's computer.

## First launch

- Windows: if SmartScreen appears, click "More info" → "Run anyway".
- macOS: after moving the app to Applications, right-click it in Finder → Open, or allow it in System Settings → Privacy & Security.

## Uninstall

On Windows, uninstall 初星助手 from Settings → Apps (the NSIS installer registers `Uninstall.exe` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`). On macOS, move the app to the Trash.
The downloaded program, its Python environment and settings live in `%APPDATA%\hiro-console` (Windows) or
`~/Library/Application Support/hiro-console` (macOS); delete that folder to remove them as well.

---

## 代码签名政策（中文）

安装包**目前没有代码签名**，第一次打开时系统会拦一下：Windows 的 SmartScreen 点「更多信息 → 仍要运行」；macOS 把应用拖进「应用程序」后，在 Finder 里右键 → 打开，或者到「系统设置 → 隐私与安全性」里允许。

- 安装包由本仓库的 `desktop` 工作流从源码构建；以后拿到证书，也只签这个工作流产出的文件，随包的 uv 是上游原样的二进制，不用本项目的证书签，每次签名由维护者人工批准。
- 卸载：Windows 在「设置 → 应用」里卸载；下载的程序、Python 环境和设置在 `%APPDATA%\hiro-console`（Mac 是 `~/Library/Application Support/hiro-console`），删掉这个目录即可。
- 隐私：程序只在用户操作时联网——下载本仓库、Python 和依赖、公开的游戏数据和模型；玩的时候把游戏画面和状态发给用户自己配置的大模型服务；不收集使用数据，API Key 只存在本机。
