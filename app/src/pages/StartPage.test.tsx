import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HiroCtx } from "../lib/hiro";
import { choose, fakeCaller } from "../test/fake";
import { StartPage } from "./StartPage";

afterEach(cleanup);

function setup() {
  const fake = fakeCaller({
    "plan show": { ok: false, exit: 1, error: { code: "no_plan", message: "还没有编成方案" } },
    "run start": (args) => ({ ok: true, exit: 3, data: {}, pending: { id: "p1", command: "run start", summary: `确认：${args.slice(2).join(" ")}`, expires_at: 1 } }),
  });
  render(
    <HiroCtx.Provider value={fake.call}>
      <StartPage onOpenJob={() => {}} />
    </HiroCtx.Provider>,
  );
  return fake;
}

const started = (fake: ReturnType<typeof fakeCaller>) => fake.calls.filter((c) => c[0] === "run" && c[1] === "start").map((c) => c.slice(2).join(" "));

test("五种模式都在，「初」填偶像卡和难度后出确认", async () => {
  const fake = setup();
  for (const name of ["H.I.F", "初", "N.I.A", "偶像之路", "日常"]) expect(screen.getByRole("button", { name: new RegExp(`^${name.replace(".", "\\.")}`) })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /^初/ }));
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "i_card-skin-shro-3-007" } });
  fireEvent.click(screen.getByRole("radio", { name: "PRO" }));
  fireEvent.click(screen.getByRole("button", { name: "准备开局" }));
  await waitFor(() => expect(started(fake)).toEqual(["hajime --command new --idol i_card-skin-shro-3-007 --difficulty pro"]));
  expect(await screen.findByRole("button", { name: "开始" })).toBeTruthy();
});

test("H.I.F 本战带主属性和编成号", async () => {
  const fake = setup();
  fireEvent.click(screen.getByRole("button", { name: /^H\.I\.F/ }));
  fireEvent.click(screen.getByRole("radio", { name: "本战" }));
  fireEvent.change(screen.getByLabelText("偶像卡"), { target: { value: "i_card-skin-shro-3-007" } });
  await choose("主属性", "Da");
  fireEvent.change(screen.getByLabelText("支援卡编成号"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("回忆编成号"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "准备开局" }));
  await waitFor(() =>
    expect(started(fake)).toEqual(["hif --command new --mode main --idol i_card-skin-shro-3-007 --main-attr Da --support-set 4 --memory-set 5"]),
  );
});

test("接着打进行中的培育不用填偶像卡", async () => {
  const fake = setup();
  fireEvent.click(screen.getByRole("button", { name: /^N\.I\.A/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "接着打进行中的培育" }));
  fireEvent.click(screen.getByRole("button", { name: "准备开局" }));
  await waitFor(() => expect(started(fake)).toEqual(["nia --command resume --difficulty pro"]));
});

test("日常可以只选几项；偶像之路填路线和层数", async () => {
  const fake = setup();
  fireEvent.click(screen.getByRole("button", { name: /^日常/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: "工作" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "竞赛" }));
  fireEvent.click(screen.getByRole("button", { name: "准备开局" }));
  await waitFor(() => expect(started(fake)).toEqual(["daily --items activity_funds,presents,mission_reward,club_reward"]));
  fireEvent.click(screen.getByRole("button", { name: /^偶像之路/ }));
  fireEvent.change(screen.getByLabelText("路线（偶像名）"), { target: { value: "篠澤広" } });
  fireEvent.change(screen.getByLabelText("打几层"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "准备开局" }));
  await waitFor(() => expect(started(fake)[1]).toBe("tower --idol 篠澤広 --layers 3"));
});

test("新开一局没填偶像卡时按钮不可用", () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: /^初/ }));
  expect((screen.getByRole("button", { name: "准备开局" }) as HTMLButtonElement).disabled).toBe(true);
});
