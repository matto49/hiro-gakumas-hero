// 表单的小件：带标签的输入、分段单选、勾选。开局页、编成页、设置页共用。
import type { ReactNode } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { cx } from "./ui";

export const inputCls =
  "h-8 w-full rounded-md border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none transition-colors placeholder:text-ink-3 focus:border-brand focus:ring-3 focus:ring-brand/25";

export interface ChoiceOption<T extends string> {
  value: T;
  label: string;
}

/** 下拉框（UI 库的 Select，不用原生的 select 元素：原生的在 macOS 上是系统灰色菜单，和界面不搭）。
 *  选项少于 2 个时也照样能点开，选中项在触发按钮上显示它的文字。 */
export function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  placeholder,
  className,
}: {
  label: string;
  value: T | null;
  options: ChoiceOption<T>[];
  onChange: (v: T) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <Select<T> items={options} value={value} onValueChange={(v) => v !== null && onChange(v)}>
      <SelectTrigger
        aria-label={label}
        className={cx("h-8 w-full rounded-md border-line-strong bg-surface text-[13px] text-ink focus-visible:border-brand focus-visible:ring-brand/25", className)}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} className="text-[13px]">
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value} className="text-[13px]">
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function Field({ label, hint, children, className, as = "label" }: { label: string; hint?: ReactNode; children: ReactNode; className?: string; as?: "label" | "div" }) {
  const Tag = as;
  return (
    <Tag className={cx("flex min-w-0 flex-col gap-1", className)}>
      <span className="text-[12px] font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-[11.5px] leading-snug text-ink-3">{hint}</span>}
    </Tag>
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
