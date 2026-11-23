"""`hiro secret …`：账号密码（设计稿 §10）。

- 值只由人填写：`hiro secret set <名>` 只从 stdin 读（桌面程序的设置页把输入框的值写进 stdin），不收命令行参数。
- 存在系统的凭据库里：Mac 钥匙串（Security.framework）、Windows 凭据管理器（advapi32）。本机只另存名字清单。
- 没有任何命令会打印值：list 只给名字；copy 把值带敏感标记写进剪贴板，45 秒后清空（期间用户又复制了别的就不清）。
  报错信息里也不带值。
- HIRO_SECRET_BACKEND=memory：测试用的进程内后端。
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys

from hiro.cli.home import ensure_home
from hiro.cli.registry import Arg, CliError, Risk, command

SERVICE = "hiro-console"
CLEAR_AFTER = 45
_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


# ============================================================================ 后端
class MemoryBackend:
    store: dict[str, str] = {}  # noqa: RUF012

    def set(self, name: str, value: str) -> None:
        self.store[name] = value

    def get(self, name: str) -> str | None:
        return self.store.get(name)

    def delete(self, name: str) -> None:
        self.store.pop(name, None)


class MacKeychain:
    """login 钥匙串的通用密码项（服务 hiro-console，账户 = 名字）。值经内存传给 Security.framework，不出现在命令行参数里。"""

    NOT_FOUND, DUPLICATE = -25300, -25299

    def __init__(self):
        self.sec = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Security.framework/Security")
        self.cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        vp, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        self.sec.SecKeychainAddGenericPassword.argtypes = [
            vp,
            u32,
            ctypes.c_char_p,
            u32,
            ctypes.c_char_p,
            u32,
            ctypes.c_char_p,
            ctypes.POINTER(vp),
        ]
        self.sec.SecKeychainFindGenericPassword.argtypes = [
            vp,
            u32,
            ctypes.c_char_p,
            u32,
            ctypes.c_char_p,
            ctypes.POINTER(u32),
            ctypes.POINTER(vp),
            ctypes.POINTER(vp),
        ]
        self.sec.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, ctypes.c_char_p]
        self.sec.SecKeychainItemFreeContent.argtypes = [vp, vp]
        self.sec.SecKeychainItemDelete.argtypes = [vp]
        for f in (
            "SecKeychainAddGenericPassword",
            "SecKeychainFindGenericPassword",
            "SecKeychainItemModifyAttributesAndData",
            "SecKeychainItemFreeContent",
            "SecKeychainItemDelete",
        ):
            getattr(self.sec, f).restype = i32
        self.cf.CFRelease.argtypes = [vp]

    def _find(self, name: str, want_data: bool):
        s, a = SERVICE.encode(), name.encode()
        n, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        st = self.sec.SecKeychainFindGenericPassword(
            None,
            len(s),
            s,
            len(a),
            a,
            ctypes.byref(n) if want_data else None,
            ctypes.byref(data) if want_data else None,
            ctypes.byref(item),
        )
        if st == self.NOT_FOUND:
            return None, None
        if st != 0:
            raise CliError("keychain", f"钥匙串读取失败（{st}）", "在「钥匙串访问」里允许本程序访问后重试")
        value = None
        if want_data:
            value = ctypes.string_at(data, n.value).decode("utf-8")
            self.sec.SecKeychainItemFreeContent(None, data)
        return value, item

    def set(self, name: str, value: str) -> None:
        s, a, v = SERVICE.encode(), name.encode(), value.encode("utf-8")
        st = self.sec.SecKeychainAddGenericPassword(None, len(s), s, len(a), a, len(v), v, None)
        if st == self.DUPLICATE:
            _, item = self._find(name, False)
            st = self.sec.SecKeychainItemModifyAttributesAndData(item, None, len(v), v)
            self.cf.CFRelease(item)
        if st != 0:
            raise CliError("keychain", f"写入钥匙串失败（{st}）", "在「钥匙串访问」里允许本程序访问后重试")

    def get(self, name: str) -> str | None:
        value, item = self._find(name, True)
        if item:
            self.cf.CFRelease(item)
        return value

    def delete(self, name: str) -> None:
        _, item = self._find(name, False)
        if item:
            self.sec.SecKeychainItemDelete(item)
            self.cf.CFRelease(item)


class WinCredentials:
    """Windows 凭据管理器的通用凭据（目标名 hiro-console:<名字>）。"""

    CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ERROR_NOT_FOUND = 1, 2, 1168

    class CREDENTIAL(ctypes.Structure):
        from ctypes import wintypes as _w

        _fields_ = [
            ("Flags", _w.DWORD),
            ("Type", _w.DWORD),
            ("TargetName", _w.LPWSTR),
            ("Comment", _w.LPWSTR),
            ("LastWritten", _w.FILETIME),
            ("CredentialBlobSize", _w.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", _w.DWORD),
            ("AttributeCount", _w.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", _w.LPWSTR),
            ("UserName", _w.LPWSTR),
        ]

    def __init__(self):
        self.api = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]

    def _target(self, name: str) -> str:
        return f"{SERVICE}:{name}"

    def set(self, name: str, value: str) -> None:
        blob = value.encode("utf-16-le")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        c = self.CREDENTIAL(
            Type=self.CRED_TYPE_GENERIC,
            TargetName=self._target(name),
            CredentialBlobSize=len(blob),
            CredentialBlob=buf,
            Persist=self.CRED_PERSIST_LOCAL_MACHINE,
            UserName=name,
        )
        if not self.api.CredWriteW(ctypes.byref(c), 0):
            raise CliError("credentials", f"写入凭据管理器失败（{ctypes.get_last_error()}）", "重试；一直失败请反馈")

    def get(self, name: str) -> str | None:
        p = ctypes.POINTER(self.CREDENTIAL)()
        if not self.api.CredReadW(self._target(name), self.CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            if ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                return None
            raise CliError("credentials", f"读取凭据管理器失败（{ctypes.get_last_error()}）", "重试；一直失败请反馈")
        try:
            c = p.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            self.api.CredFree(p)

    def delete(self, name: str) -> None:
        self.api.CredDeleteW(self._target(name), self.CRED_TYPE_GENERIC, 0)


def backend():
    kind = os.environ.get("HIRO_SECRET_BACKEND") or (
        "mac" if sys.platform == "darwin" else "win" if sys.platform == "win32" else ""
    )
    if kind == "memory":
        return MemoryBackend()
    if kind == "mac":
        return MacKeychain()
    if kind == "win":
        return WinCredentials()
    raise CliError("unsupported", "这个系统还不支持保存账号密码", "目前只支持 Mac 和 Windows")


# ============================================================================ 剪贴板
_MAC_WRITE = """
ObjC.import('AppKit');
const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
const s = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
const pb = $.NSPasteboard.generalPasteboard;
pb.clearContents;
pb.setStringForType(s, $.NSPasteboardTypeString);
pb.setStringForType($(''), $('org.nspasteboard.ConcealedType'));
pb.setStringForType($(''), $('org.nspasteboard.TransientType'));
String(pb.changeCount);
"""
_MAC_CLEAR = """
function run(argv) {
  ObjC.import('AppKit');
  delay(Number(argv[1]));
  const pb = $.NSPasteboard.generalPasteboard;
  if (String(pb.changeCount) === argv[0]) pb.clearContents;
}
"""


def _detach(argv: list[str]) -> None:
    kw: dict = (
        {"start_new_session": True}
        if sys.platform != "win32"
        else {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    )
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def _mac_write(value: str, clear_after: int) -> None:
    r = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", _MAC_WRITE],
        input=value.encode("utf-8"),
        capture_output=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise CliError("clipboard", "写剪贴板失败", "重试；一直失败请反馈")
    count = r.stdout.decode().strip()
    _detach(["osascript", "-l", "JavaScript", "-e", _MAC_CLEAR, count, str(clear_after)])


_WIN_CLEAR = r"""
import ctypes, sys, time
from ctypes import wintypes as w
u = ctypes.windll.user32
u.CreateWindowExW.restype = w.HWND
u.OpenClipboard.argtypes = [w.HWND]
seq = int(sys.argv[1]); time.sleep(int(sys.argv[2]))
if u.GetClipboardSequenceNumber() == seq:
    hwnd = u.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    if u.OpenClipboard(hwnd):
        u.EmptyClipboard(); u.CloseClipboard()
    u.DestroyWindow(hwnd)
"""


def _win_write(value: str, clear_after: int, u=None, k=None) -> None:
    """Windows：用一个隐藏窗口当剪贴板所有者（OpenClipboard(NULL) 时 SetClipboardData 会失败），每一步都查返回值。
    u / k 是 user32 / kernel32（测试里换成假的）。"""
    from ctypes import wintypes as w

    if u is None:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    # 句柄都是指针宽度：不设 argtypes / restype 时 ctypes 按 32 位 int 截断，64 位上 GlobalLock 会拿到坏句柄
    k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [w.UINT, ctypes.c_size_t], w.HGLOBAL
    k.GlobalLock.argtypes, k.GlobalLock.restype = [w.HGLOBAL], ctypes.c_void_p
    k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [w.HGLOBAL], w.BOOL
    k.GlobalFree.argtypes, k.GlobalFree.restype = [w.HGLOBAL], w.HGLOBAL
    u.CreateWindowExW.argtypes = [
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        w.LPVOID,
    ]
    u.CreateWindowExW.restype = w.HWND
    u.DestroyWindow.argtypes, u.DestroyWindow.restype = [w.HWND], w.BOOL
    u.OpenClipboard.argtypes, u.OpenClipboard.restype = [w.HWND], w.BOOL
    u.EmptyClipboard.restype = w.BOOL
    u.CloseClipboard.restype = w.BOOL
    u.SetClipboardData.argtypes, u.SetClipboardData.restype = [w.UINT, w.HANDLE], w.HANDLE
    u.RegisterClipboardFormatW.argtypes, u.RegisterClipboardFormatW.restype = [w.LPCWSTR], w.UINT
    u.GetClipboardSequenceNumber.restype = w.DWORD

    def fail(msg: str):
        return CliError("clipboard", f"写剪贴板失败（{msg}）", "稍后重试；一直失败请反馈")

    def put(fmt: int, raw: bytes) -> None:
        h = k.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
        if not h:
            raise fail("GlobalAlloc")
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            raise fail("GlobalLock")
        ctypes.memmove(p, raw, len(raw))
        k.GlobalUnlock(h)
        if not u.SetClipboardData(fmt, h):
            k.GlobalFree(h)  # 没交给系统的内存要自己放
            raise fail("SetClipboardData")

    hwnd = u.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    if not hwnd:
        raise fail("CreateWindowEx")
    try:
        import time

        for _ in range(20):
            if u.OpenClipboard(hwnd):
                break
            time.sleep(0.05)
        else:
            raise CliError("clipboard", "剪贴板被别的程序占着", "稍后重试")
        try:
            if not u.EmptyClipboard():
                raise fail("EmptyClipboard")
            put(13, (value + "\0").encode("utf-16-le"))  # CF_UNICODETEXT
            for name in (
                "ExcludeClipboardContentFromMonitorProcessing",
                "CanIncludeInClipboardHistory",
                "CanUploadToCloudClipboard",
            ):
                fmt = u.RegisterClipboardFormatW(name)
                if fmt:
                    put(fmt, b"\0\0\0\0")
        finally:
            u.CloseClipboard()
        seq = u.GetClipboardSequenceNumber()
    finally:
        u.DestroyWindow(hwnd)
    _detach([sys.executable, "-c", _WIN_CLEAR, str(seq), str(clear_after)])


def write_clipboard(value: str, concealed: bool, clear_after: int) -> None:
    """带敏感标记写剪贴板，clear_after 秒后清空（剪贴板期间没被别人改过才清）。"""
    assert concealed
    if sys.platform == "darwin":
        _mac_write(value, clear_after)
    elif sys.platform == "win32":
        _win_write(value, clear_after)
    else:
        raise CliError("unsupported", "这个系统还不支持复制密码", "目前只支持 Mac 和 Windows")


# ============================================================================ 名字清单
def _index_path():
    return ensure_home() / "secrets.json"


def _names() -> list[str]:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_names(names: list[str]) -> None:
    _index_path().write_text(json.dumps(sorted(set(names)), ensure_ascii=False), encoding="utf-8")


def _check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise CliError("bad_args", "名字只能用字母、数字和 _ . @ -（最长 64）", "例如 game-account、game-password")
    return name


# ============================================================================ 命令
@command(
    "secret set",
    Risk.READ,
    "保存一个账号或密码（值只从标准输入读，只由用户在设置页填写）。",
    args=(Arg("name", "名字，例如 game-account、game-password"),),
    stdin=True,
    agent=False,
)
def _set(a: dict) -> dict:
    name = _check_name(a["name"])
    value = sys.stdin.read().rstrip("\r\n")
    if not value:
        raise CliError("empty_secret", "没有读到要保存的值", "在设置页的输入框里填好再保存")
    backend().set(name, value)
    _save_names(_names() + [name])
    return {"saved": name}


@command("secret list", Risk.READ, "列出保存了哪些账号密码（只有名字，不显示值）。")
def _list(a: dict) -> dict:
    return {"secrets": [{"name": n, "saved": True} for n in _names()]}


@command(
    "secret copy",
    Risk.READ,
    "把一个账号或密码复制到剪贴板（带敏感标记，45 秒后自动清空），用来粘贴进游戏登录框。不会显示值。",
    args=(Arg("name", "名字，例如 game-account"),),
)
def _copy(a: dict) -> dict:
    name = _check_name(a["name"])
    value = backend().get(name) if name in _names() else None
    if not value:
        raise CliError("secret_missing", f"没有保存 {name}", "请用户在设置 → 账号密码里填写")
    write_clipboard(value, concealed=True, clear_after=CLEAR_AFTER)
    return {"copied": name, "clears_in": CLEAR_AFTER}


@command("secret delete", Risk.READ, "删除一个保存的账号或密码。", args=(Arg("name", "名字"),), agent=False)
def _delete(a: dict) -> dict:
    name = _check_name(a["name"])
    backend().delete(name)
    _save_names([n for n in _names() if n != name])
    return {"deleted": name}
