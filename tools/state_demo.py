"""打印示例局面的 LLM 上下文（演示用；局面是手工构造的，数值参考 run2 第 9 天）。

PYTHONPATH=src .venv/bin/python tools/state_demo.py [action_select|card_pick]
"""

import json
import sys

from hiro.state.produce import DeckCard, LessonOption, ProduceState

BASIC = ["p_card-02-act-0_037", "p_card-02-act-0_038", "p_card-02-men-0_039", "p_card-02-men-0_039",
         "p_card-02-men-0_039", "p_card-02-men-0_039", "p_card-02-men-0_041"]  # fmt: skip

SAMPLE = ProduceState(
    idol_card_id="i_card-shro-3-007",
    mode="produce-007",
    step=9,
    stamina=22,
    max_stamina=33,
    produce_point=388,
    params={"Vo": 609, "Da": 928, "Vi": 495},
    growth_permil={"Vo": 1449, "Da": 1579, "Vi": 1251},
    star=375,
    star_permil=1500,
    deck=[DeckCard(x) for x in BASIC]
    + [
        DeckCard("p_card-02-ido-3_105", 1),  # エウレカ！+
        DeckCard("p_card-02-men-1_031", 1),  # えいえいおー+
        DeckCard("p_card-02-act-1_037", 1, customized=True),  # デイドリーミング+（开局入手定制）
        DeckCard("p_card-02-act-2_062", 1),  # きらきら紙吹雪+
        DeckCard("p_card-02-act-3_039", 1),  # 届いて！+
        DeckCard("p_card-02-men-2_004"),  # ワクワクが止まらない
        DeckCard("p_card-02-men-2_056", 1),  # 止められない想い+
        DeckCard("p_card-02-men-2_057", 1),  # ゆめみごこち+
        DeckCard("p_card-00-acc-0_002"),  # 眠気
    ],
    drinks=["pdrink_00-2-003", "pdrink_02-3-010"],
    wappen_left=3,
    rerolls_left=2,
    excludes_left=1,
    lessons={9: [LessonOption("Vo", "Da"), LessonOption("Da", "Vo", sp=True), LessonOption("Vi", "Vo")]},
)

CANDIDATES = [
    {"card_id": "p_card-02-act-3_186"},  # せのびの魔法
    {"card_id": "p_card-02-men-2_111", "upgrade": 1},  # 成長痛+
    {"card_id": "p_card-02-act-2_049"},  # みんな大好き
]


def main() -> None:
    page = sys.argv[1] if len(sys.argv) > 1 else "action_select"
    from hiro.state.context import payload  # 游戏专用的上下文拼装，不在开源版里

    data = payload(SAMPLE, page, candidates=CANDIDATES)
    print(json.dumps(data, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
