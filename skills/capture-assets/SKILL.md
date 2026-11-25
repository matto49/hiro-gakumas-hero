---
name: hiro-capture-assets
description: 用使用者自己的考试截图补齐识别素材（考试效果图标、P 道具图标），让 やる気 / 好印象 等字段能读出来。第一次装好后打过一两场考试、或复盘发现效果名一直是 None 时用。
---

# 补素材

不补的后果：考试效果名为 None，策略层读不到 やる気 / 好印象，只能用保守规则，分数明显低。

```bash
python tools/assets/capture_icons.py collect traces/<trace>/frames --work .temp/icons   # 裁图标、聚类、出预览
python tools/assets/capture_icons.py label --work .temp/icons --llm claude:sonnet      # 或手填 labels.json
python tools/assets/capture_icons.py apply --work .temp/icons                          # 写进 HIRO_USER_ASSETS/icons/
```

- `label` 用 LLM 标名时可能标错（实测把 やる気 的拳头标成「強気」），**apply 前对照 `sheet_status.png` / `sheet_pitem.png` 逐个检查**，
  拿不准的留空，不要猜。
- 自建图库不全是正常的：没收录的效果读成 None，不会被顶成错名。
- 考试画面里没有显示 P 道具时，P 道具预览可能裁到的是背景，不是道具；这种预览留空，不要标名。
- `collect` 要用识别层判断哪些帧是考试画面，所以要在装了可选组 play 的环境里跑；如果用 `HIRO_KAA_GAME_DATA` 指向别处的游戏图，跑之前同样要设好。
- 实测（一局 R1 的 172 帧）：效果图标聚成 8 类，LLM 标对 4 类（やる気 / 好印象 / 消費体力減少 / パラメータ上昇量増加），其余被特效弄残的留空；补完后 やる気 / 好印象 能读出层数。
- 自建素材只放在本机（`resources/user_assets/` 或 `HIRO_USER_ASSETS`），**不要提交进仓库**。

详见 `docs/assets.md`。
