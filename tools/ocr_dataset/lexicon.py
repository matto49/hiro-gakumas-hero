"""游戏词表与字表（规格 §6.2 第 2 条、§6.5、§9.3）。

- 词表 `words`：识别会读到的主数据表里「名字 / 标题」类的短文字（卡、饮料、P 道具、偶像卡、支援卡、考试效果……）+ 识别代码里的界面文字常量
  （`src/hiro` 里带日文的短字符串字面量：按钮、标题、危险词）。都按 norm 归一。
- 字表 `charset`：上述表里所有文字字段（名字和说明文、效果文）出现过的字 + 界面常量的字 + 全部假名 + ASCII 可打印字符。
  剧情 / 语音 / 消息类的表不收（「綿」在主数据里只出现在剧情标题「こっそり綿あめ」，全收会让「編 / 綿」屏蔽失效；剧情我们本来就跳过）。
- `snap(text)`：按词长限定容错吸附到最近的词（≤4 字必须完全一样；5–9 字容错 1；≥10 字容错 2）；最近的词有两个以上时不猜。
"""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = re.compile(r"^(Produce|IdolCard|SupportCard|Exam|Memory|Tower|Character|Achievement|Item|Shop|Costume|Title)")
EXCLUDE = re.compile(r"Story|Voice|Message|Motion|Commu|Bgm|Timeline|Asset|Movie|Tutorial|Push")
NAME_COL = re.compile(r"name|title", re.I)
JA = re.compile(r"[぀-ヿ一-鿿]")
KANA = (
    {chr(c) for c in range(0x3041, 0x3097)} | {chr(c) for c in range(0x30A1, 0x30FB)} | {"ー", "ゝ", "ゞ", "ヽ", "ヾ"}
)
ASCII = {chr(c) for c in range(0x21, 0x7F)}
WORD_MAX = 24


FOLD = str.maketrans(dict.fromkeys("☆✦✧✩✪✫✬✭✮✯✰⭐", "★"))  # 游戏里的装饰星号，各读者写法不一，比较时当同一个


PLUS_STAR = re.compile(r"★(?=[0-9])")  # 数字前的装饰「+」常被 LLM 写成星号（实测 81 个框），按「+」算


def norm(text: str) -> str:
    """NFKC、去掉半角 / 全角空格、装饰星号归一、数字前的星号当「+」（比较 OCR 文字用）。"""
    t = unicodedata.normalize("NFKC", text or "").replace(" ", "").replace("　", "").translate(FOLD)
    return PLUS_STAR.sub("+", t)


def tolerance(n: int) -> int:
    return 0 if n <= 4 else 1 if n <= 9 else 2


def levenshtein(a: str, b: str, limit: int | None = None) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if limit is not None and min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


@dataclass
class Lexicon:
    words: set[str] = field(default_factory=set)
    charset: set[str] = field(default_factory=set)
    version: str = ""
    names: set[str] = field(default_factory=set)  # 只来自主数据的名字（卡、饮料、P 道具、偶像……），给文字类型兜底用

    def __post_init__(self) -> None:
        self.words = {norm(w) for w in self.words if norm(w)}
        self.names = {norm(w) for w in self.names if norm(w)}
        self._by_len: dict[int, list[str]] = {}
        for w in self.words:
            self._by_len.setdefault(len(w), []).append(w)

    def snap(self, text: str | None) -> str | None:
        t = norm(text or "")
        if not t:
            return None
        if t in self.words:
            return t
        best, best_d, tie = None, None, False
        for n in range(max(1, len(t) - 2), len(t) + 3):
            tol = tolerance(n)
            if abs(n - len(t)) > tol:
                continue
            for w in self._by_len.get(n, ()):
                d = levenshtein(t, w, tol)
                if d > tol:
                    continue
                if best_d is None or d < best_d:
                    best, best_d, tie = w, d, False
                elif d == best_d and w != best:
                    tie = True
        return None if tie else best


def _walk(v, out: list[str]) -> None:
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        for x in v.values():
            _walk(x, out)
    elif isinstance(v, list):
        for x in v:
            _walk(x, out)


def _decode(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def master_texts(db) -> tuple[set[str], set[str]]:
    """主数据 → (名字类短文字, 所有文字字段的字)。"""
    words, chars = set(), set()
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        if not INCLUDE.search(t) or EXCLUDE.search(t):
            continue
        cols = [r[1] for r in db.execute(f"pragma table_info('{t}')")]
        for row in db.execute(f'select * from "{t}"'):
            for col, v in zip(cols, row):
                texts: list[str] = []
                _walk(_decode(v), texts)
                for s in texts:
                    if not JA.search(s):
                        continue
                    chars.update(norm(s))
                    if NAME_COL.search(col) and "\n" not in s and len(norm(s)) <= WORD_MAX:
                        words.add(norm(s))
    return words, chars


def code_literals(src: Path = ROOT / "src" / "hiro") -> set[str]:
    """识别 / 执行代码里带日文的短字符串字面量（不含 docstring）。"""
    out = set()
    for p in src.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        docs = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant)
        }
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                s = norm(n.value)
                if 2 <= len(s) <= WORD_MAX and JA.search(s) and not re.search(r"[{}%\\\n]", n.value):
                    out.add(s)
    return out


def _version(db) -> str:
    """词表用的主数据的版本：从 catalog 实际查词的这个库取（hiro.state.master_db 生成的库记着模拟器主数据的提交）。
    拿不到版本就报错：不退回别的目录里的 version.txt / game.db，那可能是另一份数据。"""
    from hiro.state import master_db

    row = db.execute("PRAGMA database_list").fetchone()
    path = row[2] if row else ""
    version = master_db.commit_of(path) if path else None
    if not version:
        raise RuntimeError(
            f"词表用的主数据库（{path or '内存库'}）里没有版本信息：它不是 hiro.state.master_db 生成的库，"
            "没法记词表的主数据版本。先跑 python -m hiro.state.master_db 重新生成，或不要用 HIRO_MASTER_DB 指到旧库"
        )
    return version


def build() -> Lexicon:
    from hiro.state.context import catalog

    db = catalog().db
    words, chars = master_texts(db)
    ui = code_literals()
    chars |= {c for w in ui for c in w}
    return Lexicon(words=words | ui, charset=chars | KANA | ASCII, version=_version(db), names=words)


def save(lex: Lexicon, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "words.txt").write_text("\n".join(sorted(lex.words)) + "\n", encoding="utf-8")
    (out_dir / "charset.txt").write_text("".join(sorted(lex.charset)) + "\n", encoding="utf-8")
    (out_dir / "names.txt").write_text("\n".join(sorted(lex.names)) + "\n", encoding="utf-8")
    (out_dir / "version.json").write_text(json.dumps({"master": lex.version, "words": len(lex.words), "chars": len(lex.charset)},
                                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")  # fmt: skip


def load(out_dir: Path) -> Lexicon:
    words = (out_dir / "words.txt").read_text(encoding="utf-8").split("\n")
    chars = set((out_dir / "charset.txt").read_text(encoding="utf-8").rstrip("\n"))
    ver = json.loads((out_dir / "version.json").read_text(encoding="utf-8"))["master"]
    nf = out_dir / "names.txt"
    names = {w for w in nf.read_text(encoding="utf-8").split("\n") if w} if nf.is_file() else set()
    return Lexicon(words={w for w in words if w}, charset=chars, version=ver, names=names)
