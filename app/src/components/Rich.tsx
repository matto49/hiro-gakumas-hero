// 对话气泡里的简单格式：**加粗**、`代码`、以「- 」或「1. 」开头的列表行、空行分段。
// 只生成 React 元素，不用 innerHTML：模型输出里的 HTML 原样当文字显示。
import { Fragment, type ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={`${key}-b${i++}`}>{m[1]}</strong>);
    else out.push(
      <code key={`${key}-c${i++}`} className="rounded bg-chip px-1 text-[12px]">
        {m[2]}
      </code>,
    );
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const BULLET = /^\s*(?:[-*•]|\d+[.、])\s+/;

export function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      const k = `p${blocks.length}`;
      blocks.push(
        <p key={k} className="m-0">
          {para.map((line, j) => (
            <Fragment key={j}>
              {j > 0 && <br />}
              {inline(line, `${k}-${j}`)}
            </Fragment>
          ))}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      const k = `l${blocks.length}`;
      blocks.push(
        <ul key={k} className="m-0 pl-5">
          {list.map((line, j) => (
            <li key={j}>{inline(line, `${k}-${j}`)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    if (!line.trim()) {
      flushPara();
      flushList();
    } else if (BULLET.test(line)) {
      flushPara();
      list.push(line.replace(BULLET, ""));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return <div className="flex flex-col gap-1.5">{blocks}</div>;
}
