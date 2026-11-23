import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { HiroCtx } from "../lib/hiro";
import { fakeCaller } from "../test/fake";
import { IdolPicker } from "./IdolPicker";

afterEach(cleanup);

function Host({ seen }: { seen: string[] }) {
  const [v, setV] = useState("");
  return <IdolPicker value={v} onChange={(id) => (seen.push(id), setV(id))} />;
}

const IDOLS = [
  { kind: "idols", id: "i_card-shro-3-007", name: "【Campus mode!!】篠澤広", rarity: "SSR" },
  { kind: "idols", id: "i_card-shro-1-000", name: "【学園生活】篠澤広", rarity: "R" },
];

test("按名字搜偶像卡，选中后交出 skin 编号、框里显示卡名", async () => {
  const seen: string[] = [];
  const ZH = IDOLS.map((x) => ({ ...x, name: x.name.replace("篠澤広", "筱泽广") }));
  const fake = fakeCaller({
    "catalog search": (args) => ({ ok: true, exit: 0, data: { items: args.includes("zh") ? ZH : IDOLS, truncated: false } }),
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Host seen={seen} />
    </HiroCtx.Provider>,
  );
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "篠澤" } });
  await waitFor(() => expect(screen.getByRole("option", { name: /Campus mode!!】筱泽广/ })).toBeTruthy());
  expect(fake.calls.slice(-2)).toEqual([
    ["catalog", "search", "篠澤", "--kind", "idols", "--lang", "ja"],
    ["catalog", "search", "篠澤", "--kind", "idols", "--lang", "zh"],
  ]);
  expect(screen.getByRole("option", { name: /Campus mode!!/ }).textContent).toContain("【Campus mode!!】篠澤広"); // 日文原名做副标题
  fireEvent.mouseDown(screen.getByRole("option", { name: /Campus mode!!/ }));
  expect(seen.at(-1)).toBe("i_card-skin-shro-3-007");
  expect((screen.getByLabelText("偶像卡") as HTMLInputElement).value).toBe("【Campus mode!!】筱泽广");
  expect(screen.queryByRole("option")).toBeNull();
});

test("直接填编号也行；资料库没下载时提示可以填编号", async () => {
  const seen: string[] = [];
  const fake = fakeCaller({ "catalog search": { ok: false, exit: 1, error: { code: "catalog_missing", message: "资料库还没下载", hint: "运行 hiro catalog sync" } } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Host seen={seen} />
    </HiroCtx.Provider>,
  );
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "i_card-skin-shro-3-007" } });
  expect(seen.at(-1)).toBe("i_card-skin-shro-3-007");
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "篠澤" } });
  await waitFor(() => expect(screen.getByText(/资料库还没下载/)).toBeTruthy());
  expect(seen.at(-1)).toBe("");
});

test("搜到的卡按稀有度排：SSR 在前", async () => {
  const fake = fakeCaller({ "catalog search": { ok: true, exit: 0, data: { items: [IDOLS[1], { kind: "idols", id: "i_card-shro-2-000", name: "【SR】篠澤広", rarity: "SR" }, IDOLS[0]], truncated: false } } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Host seen={[]} />
    </HiroCtx.Provider>,
  );
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "篠澤" } });
  await waitFor(() => expect(screen.getAllByRole("option").length).toBe(3));
  expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["SSR【Campus mode!!】篠澤広", "SR【SR】篠澤広", "R【学園生活】篠澤広"]);
});

test("扫描过偶像卡：框是空的时点一下列出持有的卡；搜索结果里持有的排前面、标「持有」", async () => {
  const seen: string[] = [];
  const fake = fakeCaller({
    "info idols": { ok: true, exit: 0, data: { kind: "idols", scanned_at: "2026-11-23 10:00:00", items: [{ id: "i_card-shro-1-000", name: "【学園生活】篠澤広", rarity: "R" }] } },
    "catalog search": { ok: true, exit: 0, data: { items: IDOLS, truncated: false } },
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Host seen={seen} />
    </HiroCtx.Provider>,
  );
  await waitFor(() => expect(fake.calls.some((c) => c.join(" ") === "info idols")).toBe(true));
  fireEvent.focus(screen.getByLabelText("偶像卡"));
  await waitFor(() => expect(screen.getByRole("option", { name: /学園生活/ })).toBeTruthy());
  expect(screen.getAllByRole("option")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "篠澤" } });
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
  const opts = screen.getAllByRole("option");
  expect(opts[0]!.textContent).toContain("学園生活"); // R 但持有：排在没持有的 SSR 前面
  expect(opts[0]!.textContent).toContain("持有");
  expect(opts[1]!.textContent).not.toContain("持有");
});
