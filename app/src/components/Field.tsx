// 表单的小件：带标签的输入、分段单选、勾选。开局页、编成页、设置页共用。
import type { ReactNode } from "react";
import { cx } from "./ui";

export const inputCls =
  "h-8 w-full rounded-md border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none transition-colors placeholder:text-ink-3 focus:border-brand focus:ring-3 focus:ring-brand/25";

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-1", className)}>
      <span className="text-[12px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-[11.5px] leading-snug text-ink-3">{hint}</span>}
    </label>
  );
}

/** 分段单选：每段是真的 radio（键盘、读屏都能用），选中的是白底描边。 */
export function Segmented<T extends string>({ name, label, value, options, onChange }: { name: string; label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-1 border-0 p-0">
      <legend className="mb-1 p-0 text-[12px] font-medium text-ink-2">{label}</legend>
      <div className="inline-flex w-fit rounded-md bg-surface-3 p-0.5">
        {options.map(([v, text]) => (
          <label
            key={v}
            className={cx(
              "relative cursor-pointer rounded-[5px] px-3 py-1 text-[12.5px] transition-colors",
              value === v ? "bg-surface font-semibold text-ink shadow-[0_1px_2px_oklch(0.2_0.005_60/0.12)]" : "text-ink-2 hover:text-ink",
            )}
          >
            <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} className="sr-only" aria-label={text} />
            {text}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function Check({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink">
      <input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} className="size-4 accent-[var(--color-brand-deep)]" />
      <span>{label}</span>
      {hint && <span className="text-[11.5px] text-ink-3">{hint}</span>}
    </label>
  );
}
