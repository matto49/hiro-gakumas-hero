// 信息页：标题旁写上次扫描时间和扫描按钮；筛选；紧凑列表（一行一条，像桌面工具，不做卡片墙）。
import { useEffect, useMemo, useState, type ReactElement } from "react";
import { NameStack, RowTip, Thumb, TipSection } from "../components/CatalogBits";
import { inputCls } from "../components/Field";
import { StartTask } from "../components/StartTask";
import { Button, Chip, Empty, PageTitle, cx } from "../components/ui";
import { ATTR_ZH, TYPE_ZH, activeAbilities, loadSupports, spRate, useLookup, type Lookup, type Supports } from "../lib/catalog";
import { HiroError, data, useHiro, type Listing } from "../lib/hiro";
import { ATTR_STYLE, TIMING_LABEL } from "../lib/labels";

type Kind = "supports" | "memories" | "idols" | "affection" | "catalog" | "game_settings";
type Row = Record<string, unknown>;

const TITLE: Record<Kind, string> = { supports: "支援卡", memories: "回忆", idols: "偶像卡", affection: "好感度", catalog: "资料库", game_settings: "游戏设置" };

/** 有扫描任务的信息：[任务种类, 第一次的按钮字, 之后的按钮字] */
const SCAN: Partial<Record<Kind, [string, string, string]>> = {
  idols: ["scan-idols", "扫描", "重新扫描"],
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
    ...["Vo", "Da", "Vi"].map((x) => ({ label: ATTR_ZH[x]!, test: (r: Row) => r.attr === x })),
  ],
  memories: ["start", "mid", "final"].map((x) => ({ label: TIMING_LABEL[x]!, test: (r: Row) => r.timing === x })),
  idols: ["SSR", "SR", "R"].map((x) => ({ label: x, test: (r: Row) => r.rarity === x })),
};

const RARITY_TONE: Record<string, string> = { SSR: "text-brand-ink", SR: "text-da-strong", R: "text-ink-3" };

/** 属性标：声乐 / 舞蹈 / 视觉（辅助卡没有属性色）。 */
function AttrBadge({ attr, type }: { attr: unknown; type?: string }) {
  const label = ATTR_ZH[String(attr)] ?? (type ? TYPE_ZH[type] : undefined) ?? "?";
  return (
    <span className={cx("grid h-6 w-10 flex-none place-items-center rounded-[5px] text-[11px] font-bold", ATTR_STYLE[String(attr)] ?? "bg-surface-3 text-ink-2")}>
      {label}
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

/** 页面级的资料：支援卡资料、按编号查到的中日文名 / 图 / 效果。 */
interface Extra {
  supports: Supports | null;
  look: { zh: Lookup; ja: Lookup; missing: boolean } | null;
}

function SupportCell({ r, extra }: { r: Row; extra: Extra }) {
  const info = extra.supports?.cards[String(r.id)];
  const level = typeof r.level === "number" ? r.level : null;
  const breaks = typeof r.breaks === "number" ? r.breaks : null;
  const cap = info?.caps && breaks != null ? info.caps[breaks] : null;
  const abilities = info ? activeAbilities(info, level) : [];
  const sp = info ? spRate(info) : null;
  const tip = info && (
    <>
      <div className="text-[13px] font-semibold text-ink">{info.name.zh ?? info.name.ja}</div>
      <div className="jp text-[11.5px] text-ink-3">{info.name.ja}</div>
      <div className="mt-1 text-[11.5px] text-ink-2">
        {TYPE_ZH[info.type] ?? info.type} · {info.rarity} · Lv {level ?? "?"}
        {cap ? ` / ${cap}` : ""} · 突破 {breaks ?? "?"}
      </div>
      {sp && <TipSection title="SP 发生率">{sp}</TipSection>}
      <TipSection title={`现在的能力（Lv ${level ?? "?"}）`}>
        {abilities.length ? (
          <ul className="m-0 list-none p-0">
            {abilities.map((a, i) => (
              <li key={i}>· {a}</li>
            ))}
          </ul>
        ) : (
          "没有"
        )}
      </TipSection>
      {info.events && info.events.length > 0 && (
        <TipSection title="事件">
          <ul className="m-0 list-none p-0">
            {info.events.map((e, i) => (
              <li key={i} className={cx(level != null && e.level > level && "text-ink-3")}>
                · Lv {e.level}：{e.zh ?? e.ja}
              </li>
            ))}
          </ul>
        </TipSection>
      )}
    </>
  );
  return {
    tip,
    cell: (
      <>
        <Thumb asset={`support-${String(r.id)}`} />
        <AttrBadge attr={r.attr} type={info?.type} />
        <NameStack zh={info?.name.zh} ja={String(r.name ?? info?.name.ja ?? "") || null} />
        <span className={cx("w-9 flex-none text-[11.5px] font-bold", RARITY_TONE[String(r.rarity)] ?? "text-ink-3")}>{String(r.rarity ?? "?")}</span>
        <span className="w-14 flex-none text-[12px] text-ink-2 tabular-nums">Lv {String(r.level ?? "?")}</span>
        <Breaks n={r.breaks} />
      </>
    ),
  };
}

function MemoryCell({ r, extra }: { r: Row; extra: Extra }) {
  const st = (r.stats ?? {}) as Record<string, number | null>;
  const ab = (r.abilities ?? []) as string[];
  const id = String(r.card_id ?? "");
  const zh = extra.look?.zh.items[id];
  const abZh = ab.map((a) => extra.look?.zh.texts[a] ?? a);
  const tip = (zh?.text || ab.length > 0) && (
    <>
      <div className="text-[13px] font-semibold text-ink">{zh?.name ?? String(r.card ?? "")}</div>
      {zh?.name && r.card ? <div className="jp text-[11.5px] text-ink-3">{String(r.card)}</div> : null}
      {zh?.text && <TipSection title="带入的技能卡效果">{zh.text}</TipSection>}
      <TipSection title="属性加成">
        声乐 {st.Vo ?? "?"} · 舞蹈 {st.Da ?? "?"} · 视觉 {st.Vi ?? "?"}
        {st.hp != null ? ` · 体力 ${st.hp}` : ""}
      </TipSection>
      {abZh.length > 0 && (
        <TipSection title="能力">
          <ul className="m-0 list-none p-0">
            {abZh.map((a, i) => (
              <li key={i} className={cx(a === ab[i] && "jp")}>
                · {a}
              </li>
            ))}
          </ul>
        </TipSection>
      )}
    </>
  );
  return {
    tip,
    cell: (
      <>
        <Thumb asset={zh?.asset} />
        <span className="w-10 flex-none rounded-[5px] bg-surface-3 text-center text-[11px] leading-6 text-ink-2">{TIMING_LABEL[String(r.timing)] ?? "?"}</span>
        <NameStack
          zh={zh?.name}
          ja={r.card ? String(r.card) : null}
          sub={abZh.length > 0 ? <div className="truncate text-[11px] text-ink-3">{abZh.join(" / ")}</div> : undefined}
        />
        {(["Vo", "Da", "Vi"] as const).map((a) => (
          <span key={a} className="w-[60px] flex-none text-right text-[12px] tabular-nums">
            <span className={cx("mr-1 text-[10.5px] font-bold", a === "Vo" ? "text-vo-strong" : a === "Da" ? "text-da-strong" : "text-vi-ink")}>{a}</span>
            {st[a] ?? "?"}
          </span>
        ))}
      </>
    ),
  };
}

function GenericCell({ r, extra }: { r: Row; extra: Extra }) {
  const id = String(r.id ?? "");
  const zh = extra.look?.zh.items[id];
  const ja = extra.look?.ja.items[id];
  const meta = [r.rarity, r.level != null && `Lv ${r.level}`].filter(Boolean).join(" · ");
  const text = zh?.text ?? (typeof r.text === "string" ? r.text : null);
  const asset = zh?.asset ?? (typeof r.asset === "string" ? r.asset : null);
  const jaName = ja?.name ?? (typeof r.name === "string" ? r.name : typeof r.idol === "string" ? r.idol : null);
  const tip = text && (
    <>
      <div className="text-[13px] font-semibold text-ink">{zh?.name ?? jaName}</div>
      {zh?.name && jaName && zh.name !== jaName && <div className="jp text-[11.5px] text-ink-3">{jaName}</div>}
      <TipSection title="效果">
        <span className="whitespace-pre-line">{text}</span>
      </TipSection>
    </>
  );
  return {
    tip,
    cell: (
      <>
        <Thumb asset={asset} />
        <NameStack zh={zh?.name} ja={jaName ?? id} sub={text ? <div className="truncate text-[11px] text-ink-3">{text.replace(/\n/g, " ")}</div> : undefined} />
        {meta && <span className="flex-none text-[11.5px] text-ink-3">{meta}</span>}
      </>
    ),
  };
}

function SettingRow({ r }: { r: Row }) {
  return { tip: null, cell: <SettingCell r={r} /> };
}

type CellFn = (p: { r: Row; extra: Extra }) => { tip: ReactElement | null | false | undefined | ""; cell: ReactElement };
const CELL: Partial<Record<Kind, CellFn>> = { supports: SupportCell, memories: MemoryCell, game_settings: SettingRow };

/** 资料库没同步时的提示条：点了马上同步（只下载公开资料，不动游戏）。 */
function SyncBar({ onDone }: { onDone: () => void }) {
  const call = useHiro();
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="mb-3 flex items-center gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12.5px] text-ink-2">
      <span className="min-w-0 flex-1">还没同步资料库：现在显示的是游戏里的日文原名。同步后显示中文名、卡面小图和说明（从初星攻略站下载一次，存在本机）。</span>
      {msg && <span className="text-[12px] text-critical">{msg}</span>}
      <Button
        disabled={state === "busy"}
        onClick={async () => {
          setState("busy");
          setMsg(null);
          try {
            await data(call, ["catalog", "sync"]);
            onDone();
          } catch (e) {
            setState("error");
            setMsg(e instanceof HiroError ? e.error.message : String(e));
            return;
          }
          setState("idle");
        }}
      >
        {state === "busy" ? "同步中…" : "同步资料库"}
      </Button>
    </div>
  );
}

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
      // 日文、中文各搜一次合起来：输入「エウレカ」或「尤里卡」都能搜到
      Promise.all(["ja", "zh"].map((lang) => data<Listing>(call, ["catalog", "search", q.trim(), "--lang", lang])))
        .then(([ja, zh]) => {
          const seen = new Set<string>();
          const items = [...ja!.items, ...zh!.items].filter((r) => {
            const id = String(r.id);
            if (seen.has(id)) return false;
            seen.add(id);
            return true;
          });
          setListing({ kind: "catalog", scanned_at: null, items });
        })
        .catch((e) => setErr(e instanceof HiroError ? `${e.error.message}。${e.error.hint ?? ""}` : String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [kind, q, call]);

  // 资料库：支援卡资料、按编号查中文名 / 图 / 效果（只读本机文件）
  const [supports, setSupports] = useState<Supports | null>(null);
  const [supportsMissing, setSupportsMissing] = useState(false);
  const [syncTick, setSyncTick] = useState(0);
  useEffect(() => {
    if (kind !== "supports") return;
    loadSupports(call).then(
      (s) => {
        setSupports(s);
        setSupportsMissing(s === null);
      },
      () => setSupports(null),
    );
  }, [kind, call, syncTick]);
  const ids = useMemo(() => {
    const items = listing?.items ?? [];
    if (kind === "memories") return items.map((r) => String(r.card_id ?? ""));
    if (kind === "idols" || kind === "catalog") return items.map((r) => String(r.id ?? ""));
    return [];
  }, [listing, kind]);
  const texts = useMemo(() => (kind === "memories" ? (listing?.items ?? []).flatMap((r) => (r.abilities ?? []) as string[]) : []), [listing, kind]);
  const look = useLookup(call, syncTick ? [...ids, ""] : ids, texts);
  const extra: Extra = { supports, look };
  const missing = (kind === "supports" && supportsMissing) || (look?.missing ?? false);

  const filters = FILTERS[kind] ?? [];
  const items = useMemo(() => {
    const f = filters.find((x) => x.label === on);
    return (listing?.items ?? []).filter((r) => !f || f.test(r));
  }, [listing, on, filters]);
  const Cell: CellFn = CELL[kind] ?? GenericCell;

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
          placeholder="搜卡名或效果，中文日文都行，例如 エウレカ、好调"
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
      {missing && kind !== "game_settings" && (
        <SyncBar
          onDone={() => {
            setSupportsMissing(false);
            setSyncTick((n) => n + 1);
          }}
        />
      )}
      <div className="min-h-0 flex-1 overflow-auto pb-6">
        {err && <Empty>{err}</Empty>}
        {!err && listing && listing.items.length === 0 && kind !== "catalog" && (
          <Empty>
            <div>{SCAN[kind] ? `还没${SCAN[kind]![1]}过：游戏停在首页时点右上角的「${SCAN[kind]![1]}」。` : (listing.hint ?? "没有数据")}</div>
            {!SCAN[kind] && (
              <button type="button" className="mt-2 text-[12px] font-semibold text-brand-deep hover:underline" onClick={() => onAsk(`${TITLE[kind]}怎么看`)}>
                问问初星助手 ›
              </button>
            )}
          </Empty>
        )}
        {!err && listing && listing.items.length === 0 && kind === "catalog" && q.trim() && <Empty>没搜到</Empty>}
        {items.length > 0 && (
          <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface p-0">
            {items.map((r, i) => {
              const { tip, cell } = Cell({ r, extra });
              return (
                <RowTip key={String(r.id ?? r.card_id ?? i) + ":" + i} content={tip || null}>
                  <li className="flex min-h-11 items-center gap-3 px-3.5 py-2 hover:bg-surface-2">{cell}</li>
                </RowTip>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
