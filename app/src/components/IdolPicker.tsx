// 偶像卡选择：按名字搜资料库（catalog search --kind idols），点一张就交出 skin 编号（开局脚本要的那种）。
// 直接填 i_card-… 编号也行（资料库没下载时用）。
import { useEffect, useRef, useState } from "react";
import { HiroError, data, useHiro } from "../lib/hiro";
import { inputCls } from "./Field";
import { cx } from "./ui";

type Idol = { id: string; name: string | null; rarity?: string | null };

const RARITY_TONE: Record<string, string> = { SSR: "text-brand-ink", SR: "text-da-strong", R: "text-ink-3" };

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

  // 外面换了值（比如编成方案读进来）且框里不是这张卡的名字时，跟着改
  useEffect(() => {
    if (!value) return;
    setText((t) => (t && !t.startsWith("i_card") ? t : initialName || value));
  }, [value, initialName]);

  const search = async (q: string) => {
    const n = ++seq.current;
    try {
      const r = await data<{ items: Idol[] }>(call, ["catalog", "search", q, "--kind", "idols"]);
      if (n !== seq.current) return;
      setItems(r.items.slice(0, 8));
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
    setText(it.name ?? it.id);
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
        onFocus={() => items.length > 0 && !value && setOpen(true)}
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
              <span className="jp min-w-0 flex-1 truncate">{it.name ?? it.id}</span>
            </li>
          ))}
        </ul>
      )}
      {note && <div className="mt-1 text-[11.5px] leading-snug text-ink-3">{note}</div>}
    </div>
  );
}
