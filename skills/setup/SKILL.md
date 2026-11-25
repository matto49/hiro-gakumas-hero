---
name: hiro-setup
description: 在使用者的 Mac 上从零装好 hiro-gakumas-hero：Python 环境、OCR 模型、游戏数据、LLM，并做一次开局前自检。第一次接手这个仓库、换机器、或依赖坏了时用。
---

# 装环境

先读仓库根目录的 `AGENTS.md` 第一节（绝对不能做的事），整个过程中都遵守。

1. 前提（问使用者确认，不要自己装或改）：macOS（Apple Silicon）、PlayCover 里装好学マス并登录、游戏窗口竖屏、MaaTools 可用（hguandl 分支 PlayCover，见 AGENTS.md）。
2. 代码、依赖、OCR 模型、公开游戏数据和主数据库，一条命令：
   ```bash
   scripts/setup.sh   # 可重复跑；已有数据目录加 --no-data（并设 HIRO_KAA_GAME_DATA / HIRO_MASTER_DB）
   ```
   自检通过后再跑一次测试：`PYTHONPATH=src .venv/bin/python -m pytest -q`（没有截图样例时会跳过一批，正常）。
3. 截图权限：macOS「系统设置 → 隐私与安全性 → 屏幕录制」要允许运行它的终端 / 程序；截图报
   `could not create image from display` 时先请使用者检查这里和显示器（不要自己改系统设置）。
4. LLM：`HIRO_LLM=claude:sonnet`（需要能运行 `claude -p`）或 `openai:<model>`（OpenAI 兼容接口，按 `src/hiro/brain/llm.py` 设密钥变量）。
   密钥只放环境变量，不写进文件、不提交。
5. 自检（游戏停在首页）：
   ```bash
   PYTHONPATH=src python tools/check_settings.py      # 读游戏内设置：快进 / 跳过演出应全 ON
   ```
   设置读成「被重置」时，按提示在游戏里打开，或让第一次开局时的设置弹窗按默认处理。
6. 素材：第一次跑完一两场考试后，用 `capture-assets` 技能补齐效果图标。

做完告诉使用者：装好了什么、哪些测试被跳过以及原因、还缺什么。
