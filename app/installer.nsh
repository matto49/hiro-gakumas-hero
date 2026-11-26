; 初星助手自己的安装脚本片段（mygo.json 的 windows.installerInclude）。文件要带 UTF-8 BOM：NSIS 读没 BOM 的脚本按本机 ANSI 码页，中文会乱。
;
; N23（G3 第 2 轮 11-26）：初星助手用「以管理员身份重新打开」（#140）后，普通权限的安装 / 卸载程序看不到它的路径、也关不掉它，
; mygo 的 CloseApp 当成没在运行：卸载照样报「卸载成功」，删了 Uninstall.exe 却留下被锁的 初星助手.exe，什么也不提示。
; 这里在一开始先走一遍 CloseApp（普通权限打开的照旧问「要不要关掉」），之后 exe 还被占着，就请用户自己关掉再来，并且什么都不动。

!macro HiroEnsureClosed UN WHAT
  Push "$INSTDIR"
  Call ${UN}CloseApp
  ; 标签在每个函数里只出现一次（这个宏在隐藏 Section、un.onInit 里各插一次），不会重名
  IfFileExists "$INSTDIR\初星助手.exe" 0 hiro_closed
  ClearErrors
  FileOpen $0 "$INSTDIR\初星助手.exe" a ; 正在运行的 exe 打不开写，打开失败就是还在跑
  IfErrors hiro_locked
  FileClose $0
  Goto hiro_closed
  hiro_locked:
  MessageBox MB_OK|MB_ICONSTOP "初星助手还开着，${WHAT}程序关不掉它（多半是用「以管理员身份重新打开」打开的）。$\r$\n$\r$\n请先关掉初星助手的窗口（任务栏上右键 → 关闭窗口），再运行${WHAT}。" /SD IDOK
  Abort
  hiro_closed:
!macroend

; 安装（含升级覆盖）：等目录页选定位置后、写文件前，对最终的 $INSTDIR 查（#151 review：不在 .onInit 里查缺省目录）。
; 这个片段在 mygo 主 Section 之前被包含，隐藏 Section（名字以 - 开头）按顺序先跑
Section "-HiroEnsureClosed"
  !insertmacro HiroEnsureClosed "" "安装"
SectionEnd

; 卸载：mygo 没有卸载开头的钩子，这里自己定义 un.onInit（mygo 的脚本里没有这个函数）
Function un.onInit
  !insertmacro HiroEnsureClosed "un." "卸载"
FunctionEnd
