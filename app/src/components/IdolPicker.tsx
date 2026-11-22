// 偶像卡选择：按名字搜资料库（catalog search --kind idols），点一张就交出 skin 编号（开局脚本要的那种）。
// 扫描过偶像卡（info idols）时：框是空的点一下就列出持有的卡；搜索结果里持有的排前面、标「持有」。
// 直接填 i_card-… 编号也行（资料库没下载时用）。
import { useEffect, useRef, useState } from "react";
import { HiroError, data, useHiro } from "../lib/hiro";
import { inputCls } from "./Field";
import { Thumb } from "./CatalogBits";
import { cx } from "./ui";

type Idol = { id: string; name: string | null; rarity?: string | null; zh?: string | null };

const RARITY_TONE: Record<string, string> = { SSR: "text-brand-ink", SR: "text-da-strong", R: "text-ink-3" };
const RARITY_RANK: Record<string, number> = { SSR: 0, SR: 1, R: 2 };
const byRarity = (a: Idol, b: Idol) => (RARITY_RANK[a.rarity ?? ""] ?? 3) - (RARITY_RANK[b.rarity ?? ""] ?? 3);

export function toSkinId(id: string): string {
  return id.startsWith("i_card-skin-") ? id : id.replace(/^i_card-/, "i_card-skin-");
}

export function IdolPicker({
  value,
  onChange,
  initialName,
  placeholder = "输入偶像名或卡名，例如 篠澤広",
  label = "偶像卡",
}: {
  value: string;
  onChange: (skinId: string) => void;
  initialName?: string | null;
  placeholder?: string;
  label?: string;
}) {
  const call = useHiro();
  const [text, setText] = useState(initialName || value);
  const [items, setItems] = useState<Idol[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [note, setNote] = useState<string | null>(null);
  const seq = useRef(0);
  const [owned, setOwned] = useState<Idol[]>([]);

  useEffect(() => {
    data<{ items: Idol[] }>(call, ["info", "idols"])
      .then((r) => setOwned((r.items ?? []).filter((x) => x.id)))
      .catch(() => {});
  }, [call]);
  const ownedIds = new Set(owned.map((x) => x.id));
  const ownedNames = new Set(owned.map((x) => x.name));
  const isOwned = (it: Idol) => ownedIds.has(it.id) || (it.name != null && ownedNames.has(it.name));
  const byOwned = (a: Idol, b: Idol) => Number(isOwned(b)) - Number(isOwned(a)) || byRarity(a, b);

  // 外面换了值（比如编成方案读进来）且框里不是这张卡的名字时，跟着改
  useEffect(() => {
    if (!value) return;
    setText((t) => (t && !t.startsWith("i_card") ? t : initialName || value));
  }, [value, initialName]);

  const search = async (q: string) => {
    const n = ++seq.current;
    try {
      // 日文、中文各搜一次合起来（中文名、日文名都能搜），显示中文名、日文原名做副标题
      const [ja, zhr] = await Promise.all(
        ["ja", "zh"].map((lang) => data<{ items: Idol[] }>(call, ["catalog", "search", q, "--kind", "idols", "--lang", lang]).catch(() => ({ items: [] as Idol[] }))),
      );
      if (n !== seq.current) return;
      const zhName = new Map(zhr!.items.map((x) => [x.id, x.name]));
      const merged = new Map<string, Idol>();
      for (const x of [...ja!.items, ...zhr!.items]) if (!merged.has(x.id)) merged.set(x.id, { ...x });
      if (!ja!.items.length && !zhr!.items.length) {
        await data(call, ["catalog", "search", q, "--kind", "idols"]); // 两边都空时再搜一次，资料库没下载的错误照常报出来
      }
      const r = { items: [...merged.values()].map((x) => ({ ...x, zh: zhName.get(x.id) ?? null, name: ja!.items.find((y) => y.id === x.id)?.name ?? x.name })) };
      setItems([...r.items].sort(byOwned).slice(0, 12));
      setNote(r.items.length ? null : "没找到这张卡，换个词试试");
      setOpen(true);
      setActive(0);
    } catch (e) {
      if (n !== seq.current) return;
      setItems([]);
      setOpen(false);
      setNote(e instanceof HiroError ? `${e.error.message}：可以直接填偶像卡编号（i_card-skin-…），或到设置里下载资料库` : "搜不了，可以直接填偶像卡编号");
    }
  };

  const type = (t: string) => {
    setText(t);
    const q = t.trim();
    if (q.startsWith("i_card-")) {
      seq.current++;
      setOpen(false);
      setNote(null);
      onChange(q);
      return;
    }
    onChange("");
    if (!q) {
      seq.current++;
      setOpen(false);
      setNote(null);
      return;
    }
    void search(q);
  };

  const pick = (it: Idol) => {
    seq.current++;
    setText(it.zh ?? it.name ?? it.id);
    setOpen(false);
    setNote(null);
    onChange(toSkinId(it.id));
  };

  const listId = `idol-list-${label}`;
  return (
    <div className="relative">
      <input
        aria-label={label}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        className={cx(inputCls, "jp")}
        placeholder={placeholder}
        value={text}
        onChange={(e) => type(e.target.value)}
        onFocus={() => {
          if (!text.trim() && owned.length) {
            setItems([...owned].sort(byRarity));
            setActive(0);
            setOpen(true);
          } else if (items.length > 0 && !value) setOpen(true);
        }}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (!open || !items.length) return;
          if (e.key === "ArrowDown") (e.preventDefault(), setActive((a) => (a + 1) % items.length));
          else if (e.key === "ArrowUp") (e.preventDefault(), setActive((a) => (a + items.length - 1) % items.length));
          else if (e.key === "Enter") (e.preventDefault(), pick(items[active]!));
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && items.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute top-[calc(100%+4px)] right-0 left-0 z-20 m-0 max-h-72 list-none overflow-auto rounded-lg border border-line bg-surface p-1 shadow-[0_8px_24px_oklch(0.2_0.01_60/0.12)]"
        >
          {items.map((it, i) => (
            <li
              key={it.id}
              role="option"
              aria-selected={i === active}
              className={cx("flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-[13px]", i === active ? "bg-brand-tint" : "hover:bg-surface-2")}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => (e.preventDefault(), pick(it))}
            >
              <span className={cx("w-8 flex-none text-[11px] font-bold", RARITY_TONE[it.rarity ?? ""] ?? "text-ink-3")}>{it.rarity ?? ""}</span>
              <Thumb asset={`idol-${it.id}`} className="size-7" />
              <span className="min-w-0 flex-1 truncate">
                <span className={cx(!it.zh && "jp")}>{it.zh ?? it.name ?? it.id}</span>
                {it.zh && it.name && it.zh !== it.name && <span className="jp ml-1.5 text-[11px] text-ink-3">{it.name}</span>}
              </span>
              {isOwned(it) && <span className="flex-none rounded bg-ok-t px-1.5 py-0.5 text-[10.5px] font-medium text-good">持有</span>}
            </li>
          ))}
        </ul>
      )}
      {note && <div className="mt-1 text-[11.5px] leading-snug text-ink-3">{note}</div>}
    </div>
  );
}
