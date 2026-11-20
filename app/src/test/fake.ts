import type { Caller, Envelope } from "../lib/hiro";

/** 假的工具层：按命令前缀回信封，记下每次调用。 */
export function fakeCaller(routes: Record<string, Envelope | ((args: string[]) => Envelope)>) {
  const calls: string[][] = [];
  const call: Caller = async (args) => {
    calls.push(args);
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => args.join(" ").startsWith(k));
    if (!key) return { ok: false, exit: 2, error: { code: "unknown_command", message: args.join(" ") } };
    const r = routes[key]!;
    return typeof r === "function" ? r(args) : r;
  };
  return { call, calls };
}
