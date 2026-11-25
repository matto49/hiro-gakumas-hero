## 下载哪个

- **Windows**：`chu-xing-zhu-shou-setup-*-amd64.exe`
- **macOS**（Apple 芯片和 Intel 都行）：`*.dmg`

文件的 SHA256 在 `SHA256SUMS.txt` 里。

## 第一次打开会被系统拦一下

安装包目前没有代码签名，第一次打开时系统会拦一下。放行一次以后就不会再问。

**macOS**（15 起「右键 → 打开」已经不管用）

1. 把「初星助手」拖进「应用程序」，双击打开。弹出「无法打开」时，点「完成」。
2. 打开「系统设置 → 隐私与安全性」，往下翻到「安全性」。在「"初星助手"已被阻止使用」旁边点「仍要打开」，然后输入开机密码。
3. 在弹出的确认框里点「打开」。

**Windows**

安装程序弹出蓝色的「Windows 已保护你的电脑」时，先点「更多信息」，再点「仍要运行」。

签名打算、隐私说明和卸载方法，见 [docs/code-signing-policy.md](https://github.com/matto49/hiro-gakumas-hero/blob/main/docs/code-signing-policy.md)。
