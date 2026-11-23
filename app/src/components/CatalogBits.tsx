// 信息页、编成方案共用的资料库小件：卡面小图（离线时用已缓存的，没有就占位）、中文名 + 日文原名、悬浮说明（UI 库的 Tooltip）。
import { useState, type ReactElement, type ReactNode } from "react";
import { ImageOff } from "lucide-react";
import { spriteUrl } from "../lib/hiro";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
import { cx } from "./ui";

/** 小缩略图：36 px 圆角，图取不到（没同步、离线没缓存）时显示占位。 */
export function Thumb({ asset, className }: { asset?: string | null; className?: string }) {
  const [broken, setBroken] = useState(false);
  const box = cx("size-9 flex-none overflow-hidden rounded-md bg-surface-3", className);
  if (!asset || broken)
    return (
      <span className={cx(box, "grid place-items-center text-ink-3")} aria-hidden>
        <ImageOff className="size-3.5 opacity-50" />
      </span>
    );
  return <img src={spriteUrl(asset)} alt="" loading="lazy" className={cx(box, "object-cover")} onError={() => setBroken(true)} />;
}

/** 卡名：中文在上，日文原名小字在下（没有中文时只显示日文）。 */
export function NameStack({ zh, ja, sub }: { zh?: string | null; ja?: string | null; sub?: ReactNode }) {
  const main = zh || ja || "（没认出）";
  return (
    <div className="min-w-0 flex-1">
      <b className={cx("block truncate text-[13px] font-medium", !zh && "jp")}>{main}</b>
      {zh && ja && zh !== ja && <div className="jp truncate text-[11px] leading-snug text-ink-3">{ja}</div>}
      {sub}
    </div>
  );
}

/** 一行的悬浮说明：鼠标停在行上显示。content 为空时不包。 */
export function RowTip({ content, children }: { content: ReactNode | null; children: ReactElement<Record<string, unknown>> }) {
  if (!content) return children;
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent side="right" align="start" sideOffset={8} className="max-w-[340px]">
        {content}
      </TooltipContent>
    </Tooltip>
  );
}

/** 悬浮说明里的一小节。 */
export function TipSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-1.5 first:mt-0">
      <div className="text-[11px] font-semibold text-ink-3">{title}</div>
      <div className="text-[12px] leading-relaxed text-ink">{children}</div>
    </div>
  );
}
