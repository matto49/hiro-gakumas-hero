# hiro-ocr-dataset

hiro-gakumas-hero（数字小广）的 OCR 测试集：游戏整帧截图 + 每个文字框的位置和正确文字 + 场景标签，用来量各个 OCR 后端在不同平台、
页面、文字类型上的字错率。设计见主仓库 `docs/ryugi/specs/2026-11-23-ocr-dataset-design.md`。

**私有仓库**：截图是游戏画面，权利属于游戏的权利人，不公开分发。图片走 Git LFS。

## 目录

| 目录 / 文件 | 内容 |
|---|---|
| `core/` | 核心集：精筛后覆盖各场景（平台 × 页面 × 文字类型） |
| `hard/` | 进阶集：失败率高的场景专门加量 |
| `inbox/<贡献者>/<日期>/` | 投稿区：图片 + meta.json，还没标注 |
| `disputed.json` | 待定清单：AI 标注时几方答案不一致的框 |
| `lexicon/` | 游戏词表 / 字表快照（生成的，带主数据版本） |
| `reports/` | 评测报告存档 |
| `schema/frame.schema.json` | 一帧标注的格式 |
| `tools/validate.py` | 校验脚本（CI 跑它，只用 Python 标准库） |

## 常用命令（在主仓库里跑）

```bash
export HIRO_OCR_DATASET=<本仓库路径>
python -m tools.ocr_dataset pick <trace 目录…> --contributor <名字>   # 投稿：挑帧打包进 inbox/
python -m tools.ocr_dataset label inbox/<贡献者>/<日期>               # 维护者：AI 标注投稿（Mac；Vision + Claude，待定框再问 Codex）
python -m tools.ocr_dataset spot-check                                # 维护者：抽 50 个 agreed 框人工核对（≥98% 才合格）
python -m tools.ocr_dataset review                                    # 维护者：核对待定框；--apply <导出的 JSON> 写回
python -m tools.ocr_dataset eval --backend rapidocr,vision --set core # 评测
python -m tools.ocr_dataset validate                                  # 校验
```
