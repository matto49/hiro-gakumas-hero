// 信息页：标题旁写上次扫描时间和扫描按钮；筛选；紧凑列表（一行一条，像桌面工具，不做卡片墙）。
import { useEffect, useMemo, useState, type ReactElement } from "react";
import { inputCls } from "../components/Field";
import { StartTask } from "../components/StartTask";
import { Chip, Empty, PageTitle, cx } from "../components/ui";
import { HiroError, data, spriteUrl, useHiro, type Listing } from "../lib/hiro";
import { ATTR_STYLE, TIMING_LABEL } from "../lib/labels";

type Kind = "supports" | "memories" | "idols" | "affection" | "catalog" | "game_settings";
type Row = Record<string, unknown>;

const TITLE: Record<Kind, string> = { supports: "支援卡", memories: "回忆", idols: "偶像卡", affection: "好感度", catalog: "资料库", game_settings: "游戏设置" };

/** 有扫描任务的信息：[任务种类, 第一次的按钮字, 之后的按钮字] */
const SCAN: Partial<Record<Kind, [string, string, string]>> = {
  supports: ["scan-supports", "扫描", "重新扫描"],
  memories: ["scan-memories", "扫描", "重新扫描"],
  game_settings: ["check-settings", "检查", "重新检查"],
};

function SettingCell({ r }: { r: Row }) {
  const on = r.state === "ON";
  return (
    <>
      <span className="min-w-0 flex-1">{String(r.item)}</span>
      <span
        className={cx(
          "rounded-full px-2 text-[11.5px] leading-5 font-semibold",
          on ? "bg-ok-t text-good" : r.state === "OFF" ? "bg-err-t text-critical" : "bg-surface-3 text-ink-3",
        )}
      >
        {r.state == null ? "读不到" : String(r.state)}
      </span>
    </>
  );
}

interface Filter {
  label: string;
  test: (r: Row) => boolean;
}

const FILTERS: Partial<Record<Kind, Filter[]>> = {
  supports: [
    ...["SSR", "SR", "R"].map((x) => ({ label: x, test: (r: Row) => r.rarity === x })),
    ...["Vo", "Da", "Vi"].map((x) => ({ label: x, test: (r: Row) => r.attr === x })),
  ],
  memories: ["start", "mid", "final"].map((x) => ({ label: TIMING_LABEL[x]!, test: (r: Row) => r.timing === x })),
  idols: ["SSR", "SR", "R"].map((x) => ({ label: x, test: (r: Row) => r.rarity === x })),
};

const RARITY_TONE: Record<string, string> = { SSR: "text-brand-ink", SR: "text-da-strong", R: "text-ink-3" };

function AttrBadge({ attr }: { attr: unknown }) {
  return (
    <span className={cx("grid h-6 w-8 flex-none place-items-center rounded-[5px] text-[11px] font-bold", ATTR_STYLE[String(attr)] ?? "bg-surface-3 text-ink-2")}>
      {String(attr ?? "?")}
    </span>
  );
}

/** 突破：游戏里是 4 个小格，点亮几个就是突破几次。 */
function Breaks({ n }: { n: unknown }) {
  const k = typeof n === "number" ? n : null;
  return (
    <span className="flex w-[72px] flex-none items-center gap-[3px]" aria-label={`突破 ${k ?? "?"}`} title={`突破 ${k ?? "?"}`}>
      {[0, 1, 2, 3].map((i) => (
        <i key={i} className={cx("h-2.5 w-3 -skew-x-[16deg] rounded-[1.5px]", k != null && i < k ? "bg-brand" : "bg-surface-3")} />
      ))}
      <span className="ml-1 text-[11px] text-ink-3 tabular-nums">{k ?? "?"}</span>
    </span>
  );
}

function SupportCell({ r }: { r: Row }) {
  return (
    <>
      <AttrBadge attr={r.attr} />
      <b className="jp min-w-0 flex-1 truncate text-[13px] font-medium">{String(r.name ?? "（没认出）")}</b>
      <span className={cx("w-9 flex-none text-[11.5px] font-bold", RARITY_TONE[String(r.rarity)] ?? "text-ink-3")}>{String(r.rarity ?? "?")}</span>
      <span className="w-14 flex-none text-[12px] text-ink-2 tabular-nums">Lv {String(r.level ?? "?")}</span>
      <Breaks n={r.breaks} />
    </>
  );
}

function MemoryCell({ r }: { r: Row }) {
  const st = (r.stats ?? {}) as Record<string, number | null>;
  const ab = (r.abilities ?? []) as string[];
  return (
    <>
      <span className="w-10 flex-none rounded-[5px] bg-surface-3 text-center text-[11px] leading-6 text-ink-2">{TIMING_LABEL[String(r.timing)] ?? "?"}</span>
      <div className="min-w-0 flex-1">
        <b className="jp block truncate text-[13px] font-medium">{String(r.card ?? "（没认出）")}</b>
        {ab.length > 0 && <div className="jp truncate text-[11.5px] text-ink-3">{ab.join(" / ")}</div>}
      </div>
      {(["Vo", "Da", "Vi"] as const).map((a) => (
        <span key={a} className="w-[60px] flex-none text-right text-[12px] tabular-nums">
          <span className={cx("mr-1 text-[10.5px] font-bold", a === "Vo" ? "text-vo-strong" : a === "Da" ? "text-da-strong" : "text-vi-ink")}>{a}</span>
          {st[a] ?? "?"}
        </span>
      ))}
    </>
  );
}

function GenericCell({ r }: { r: Row }) {
  const meta = [r.rarity, r.plan, r.level != null && `Lv ${r.level}`].filter(Boolean).join(" · ");
  return (
    <>
      {typeof r.asset === "string" ? (
        <img src={spriteUrl(r.asset)} alt="" className="size-9 flex-none rounded-md bg-surface-3" />
      ) : (
        <span className="size-9 flex-none rounded-md bg-surface-3" />
      )}
      <div className="min-w-0 flex-1">
        <b className="jp block truncate text-[13px] font-medium">{String(r.name ?? r.idol ?? r.id ?? "?")}</b>
        {typeof r.text === "string" && <div className="jp truncate text-[11.5px] text-ink-3">{r.text}</div>}
      </div>
      {meta && <span className="flex-none text-[11.5px] text-ink-3">{meta}</span>}
    </>
  );
}

const CELL: Partial<Record<Kind, (p: { r: Row }) => ReactElement>> = { supports: SupportCell, memories: MemoryCell, game_settings: SettingCell };

export function InfoPage({ kind, onAsk, onOpenJob }: { kind: Kind; onAsk: (text: string) => void; onOpenJob?: (jobId: string) => void }) {
  const call = useHiro();
  const [listing, setListing] = useState<(Listing & { verdict?: string | null }) | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [on, setOn] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    setListing(null);
    setErr(null);
    setOn(null);
    if (kind === "catalog") return;
    data<Listing>(call, ["info", kind === "game_settings" ? "game-settings" : kind])
      .then(setListing)
      .catch((e) => setErr(e instanceof HiroError ? `${e.error.message}。${e.error.hint ?? ""}` : String(e)));
  }, [kind, call]);

  useEffect(() => {
    if (kind !== "catalog") return;
    if (!q.trim()) {
      setListing({ kind, scanned_at: null, items: [] });
      return;
    }
    const t = setTimeout(() => {
      data<Listing>(call, ["catalog", "search", q.trim()])
        .then((l) => setListing({ ...l, scanned_at: null }))
        .catch((e) => setErr(e instanceof HiroError ? `${e.error.message}。${e.error.hint ?? ""}` : String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [kind, q, call]);

  const filters = FILTERS[kind] ?? [];
  const items = useMemo(() => {
    const f = filters.find((x) => x.label === on);
    return (listing?.items ?? []).filter((r) => !f || f.test(r));
  }, [listing, on, filters]);
  const Cell = CELL[kind] ?? GenericCell;

  const scanned = listing && kind !== "catalog" ? (listing.scanned_at ? `上次扫描：${listing.scanned_at.slice(0, 16)}${listing.complete === false ? " · 没扫全" : ""}` : "还没扫描") : null;
  const scan = listing && SCAN[kind];
  const groups = kind === "supports" ? [filters.slice(0, 3), filters.slice(3)] : [filters];

  return (
    <div className="flex h-full flex-col overflow-hidden px-8 py-6">
      <PageTitle
        sub={
          <>
            {scanned}
            {scanned && listing && listing.items.length > 0 && <span className="text-ink-3"> · 共 {listing.items.length} 条</span>}
          </>
        }
        right={scan ? <StartTask label={listing!.scanned_at ? scan[2] : scan[1]} args={[scan[0]]} onOpenJob={onOpenJob} /> : undefined}
      >
        {TITLE[kind]}
      </PageTitle>
      {listing?.verdict && <div className="-mt-2 mb-4 text-ink-2">{listing.verdict}</div>}
      {kind === "catalog" && (
        <input
          aria-label="搜资料库"
          className={cx(inputCls, "jp mb-4 h-9 max-w-[520px]")}
          placeholder="搜卡名或效果，例如 エウレカ、好調"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
        />
      )}
      {filters.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <Chip on={on === null} onClick={() => setOn(null)}>
            全部
          </Chip>
          {groups.map((g, gi) => (
            <span key={gi} className={cx("flex gap-1.5", gi > 0 && "ml-2 border-l border-line pl-3.5")}>
              {g.map((f) => (
                <Chip key={f.label} on={on === f.label} onClick={() => setOn(on === f.label ? null : f.label)}>
                  {f.label}
                </Chip>
              ))}
            </span>
          ))}
          {on && <span className="ml-2 text-[12px] text-ink-3">{items.length} 条</span>}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto pb-6">
        {err && <Empty>{err}</Empty>}
        {!err && listing && listing.items.length === 0 && kind !== "catalog" && (
          <Empty>
            <div>{SCAN[kind] ? `还没${SCAN[kind]![1]}过：游戏停在首页时点右上角的「${SCAN[kind]![1]}」。` : (listing.hint ?? "没有数据")}</div>
            {!SCAN[kind] && (
              <button type="button" className="mt-2 text-[12px] font-semibold text-brand-deep hover:underline" onClick={() => onAsk(`${TITLE[kind]}怎么看`)}>
                问问小广 ›
              </button>
            )}
          </Empty>
        )}
        {!err && listing && listing.items.length === 0 && kind === "catalog" && q.trim() && <Empty>没搜到</Empty>}
        {items.length > 0 && (
          <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface p-0">
            {items.map((r, i) => (
              <li key={String(r.id ?? i)} className="flex min-h-11 items-center gap-3 px-3.5 py-2 hover:bg-surface-2">
                <Cell r={r} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
