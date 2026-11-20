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
  const fake = fakeCaller({ "catalog search": { ok: true, exit: 0, data: { items: IDOLS, truncated: false } } });
  render(
    <HiroCtx.Provider value={fake.call}>
      <Host seen={seen} />
    </HiroCtx.Provider>,
  );
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "篠澤" } });
  await waitFor(() => expect(screen.getByRole("option", { name: /Campus mode!!/ })).toBeTruthy());
  expect(fake.calls.at(-1)).toEqual(["catalog", "search", "篠澤", "--kind", "idols"]);
  fireEvent.mouseDown(screen.getByRole("option", { name: /Campus mode!!/ }));
  expect(seen.at(-1)).toBe("i_card-skin-shro-3-007");
  expect((screen.getByLabelText("偶像卡") as HTMLInputElement).value).toBe("【Campus mode!!】篠澤広");
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
