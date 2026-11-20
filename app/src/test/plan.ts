// 编成方案的假数据（编成页、对话里的编成卡共用）。
export const PLAN = {
  created: "2026-11-17 16:00:00",
  idol: { id: "i_card-shro-3-007", name: "Campus mode!!", plan: "ProducePlanType_Plan2", profile: "yaruki" },
  main_attr: "Da",
  support: {
    set: 4, value: 199, counts: { Vo: 0, Da: 4, Vi: 2 },
    own: [{ id: "s1", name: "そろそろ焼けたかな？", attr: "Da", level: 60, value: 20, why: ["事件 Da+20"] }],
    rental: { name: "あなたにも作ってあげる！", attr: "Vi", value: 20, estimated: true },
  },
  memory: { set: 5, cards: [], chosen: [{ id: "p13", card_id: "c1", name: "ワクワクが止まらない", upgrade: 1, timing: "start", slot: "A", role: "核心" }] },
  notes: ["4 张集中在 Da"],
  path: "/h/formation-plan.json",
};
