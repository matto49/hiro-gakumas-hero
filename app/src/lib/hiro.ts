// 调工具层：桌面程序里走 Go 绑定（Hiro.call），信封原样带回。
import { Hiro, type Result } from "../mygo";

export type Envelope = Result;

export function hiro(args: string[]): Promise<Envelope> {
  return Hiro.call(args);
}
