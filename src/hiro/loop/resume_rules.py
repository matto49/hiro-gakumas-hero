"""续跑和结算收尾的纯判据（不依赖 KAA）：旧主循环的 hiro.loop.resume 和新主循环的 hiro.runtime.finish 共用。"""

from __future__ import annotations

from hiro.vision.textnorm import contains  # OCR 容错匹配（用户 11-21）

DISCARD_TITLE = "プロデュースデータの破棄"

RESUME_FORBIDDEN = ("破棄", "開始する")  # 有这些字就不是「再開」对话框，绝不点


def resume_button(boxes):
    """「プロデュース再開」对话框的「再開する」按钮（OCR 判，KAA 的 ButtonResume 模板在第二十局日期切换后没认出，40s 超时）。
    boxes：hiro.vision.ocr.screen_texts 的结果（.text / .rect）。只在标题「プロデュース再開」+ 按钮「再開する」都在、且没有破棄 / 開始する 时返回按钮。"""
    texts = [b.text.replace(" ", "") for b in boxes]
    if not any("プロデュース再開" in t for t in texts) or any(contains(t, k) for t in texts for k in RESUME_FORBIDDEN):
        return None
    return next((b for b in boxes if b.text.replace(" ", "").strip() == "再開する"), None)


END_BUTTON_TEXTS = ("次へ", "閉じる", "OK", "スキップ", "決定", "完了する")  # 完了する：选拔赛通过证书页
# 登录奖励 / 活动获得画面（第二十六局 4 点日切换后「学マ水曜日」ガシャチケット，点空白继续共 4 屏）：正中央是奖励道具框，
# 点中央不翻页，180s 后停机。这类画面没有按钮，点底部空白推进；有购买 / ジュエル字样一律不点
REWARD_HINTS = (
    "ログインボーナス",
    "ガシャチケット",
    "獲得",
    "開催期間",
    "受け取りました",
    "プレゼント",
    "TAP",
    "タップ",
)
REWARD_DANGER = ("購入", "ジュエル", "有償", "課金")
REWARD_TAP = (360, 1180)


def reward_tap(boxes) -> tuple[int, int] | None:
    """领奖 / 活动获得画面 → 底部空白的点击位置；不是、有危险字样、或底部那块有文字（可能是按钮）时 None。
    boxes：.text / .rect.center（kotonebot ocr）。"""
    texts = [t.text.replace(" ", "") for t in boxes]
    if not any(h in t for t in texts for h in REWARD_HINTS) or any(
        contains(t, d) for t in texts for d in REWARD_DANGER
    ):
        return None
    x, y = REWARD_TAP
    if any(abs(t.rect.center.x - x) <= 200 and abs(t.rect.center.y - y) <= 60 for t in boxes):
        return None
    return REWARD_TAP


def is_home_move_confirm(texts: list[str]) -> bool:
    """「ホーム移動確認」弹窗：标题或正文「ホームに移動しますか」「プロデュース > 本戦」任一命中。"""
    joined = "".join(t.replace(" ", "") for t in texts)
    return "ホーム移動" in joined or "ホームに移動" in joined or ("ホーム" in joined and "本戦」から開始" in joined)
