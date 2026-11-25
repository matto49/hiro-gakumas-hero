# 开发：测试、代码检查、格式、类型检查

`scripts/setup.sh` 装的 `.[dev]` 里已经带了下面这些工具（版本在 `pyproject.toml` 里钉住，本机和 CI 一致）。
只做开发、不玩实机时：`uv venv && uv pip install -e ".[dev]"`。

| 做什么 | 命令 | CI |
|---|---|---|
| 测试 | `PYTHONPATH=src .venv/bin/python -m pytest -q` | `test`：Python 3.10 / 3.12，带覆盖率（摘要页写总数，完整表格在运行附件） |
| 代码检查 | `.venv/bin/ruff check .`（能自动修的加 `--fix`） | `lint / ruff` |
| 格式 | `.venv/bin/ruff format .` | `lint / ruff`（`ruff format --check`） |
| 类型检查 | `.venv/bin/basedpyright` | `lint / typecheck` |
| 提交前自动跑 ruff | 装一次：`.venv/bin/pre-commit install`；手动全跑：`.venv/bin/pre-commit run --all-files` | — |

## 代码检查（ruff）

- 规则集在 `pyproject.toml` 的 `[tool.ruff.lint]` 里显式列出，不随 ruff 版本的缺省集合变。
- 「没用到的导入」不自动删（`F401` unfixable）：有的是给别处转出口，有的靠导入的副作用。确实不要了就手动删。
- import 分组里 `hiro` 排在第三方包前面：`import hiro`（兼容层）要先于 kaa / kotonebot，`import hiro.sim`（引擎路径）要先于 gakumas_exam 等。
- 引入 ruff 时还没修的问题标了 `# noqa: <规则>`；改到附近代码时顺手修掉、删掉 noqa。
- `noqa` 后面的说明（为什么这么写）要保留，所以没开 RUF100（自动删「用不上的 noqa」）。

## 格式（ruff format）

- 2026-11-23 全仓库统一格式化过一次（`vendor/` 除外）。这个提交登记在 `.git-blame-ignore-revs`，让 `git blame` 跳过它：

  ```bash
  git config blame.ignoreRevsFile .git-blame-ignore-revs
  ```

- 全仓库格式化（24468ce）之前开的分支：**直接合 main，不要先在分支上整仓 `ruff format .`**。实测先整仓格式化再合，冲突会从两三个文件涨到七十多个（main 那边除了格式化还有 lint 自动修复和后续改动）。
  合 main 后剩下的冲突，大多是同一处一边格式化了、一边改了内容：把共同祖先和本分支两边的版本先 `ruff format` 一下，再和 main 做三方合并（`git merge-file`），剩下的才是真冲突。
  最后只对本分支改过的 `.py` 跑 `ruff check --fix` 和 `ruff format`，再按 CI 跑一遍检查。

## 类型检查（basedpyright）

- basedpyright 是 pyright 的分支，检查结果一样，另外能记「基线」。引入时已有的 1,069 个错误记在 `.basedpyright/baseline.json`，CI 只拦**新增**的错误。
- 修掉了基线里的错误，跑 `basedpyright --writebaseline` 更新基线（只会变少，提交时一起带上）。
- 分析环境以 CI 为准：Linux、Python 3.10、只装 `.[dev]`（没有 KAA 等实机依赖）。本机 `.venv` 装了 `.[play]`，能解析出更多类型，偶尔会多报几条 CI 不报的；要复现 CI 的结果，另建一个只装 `.[dev]` 的环境跑。
- 目前只检查 `src/`。

## CI 与依赖安全

| 工作流 | 什么时候跑 | 做什么 |
|---|---|---|
| `test` | 推送、PR | pytest（3.10 / 3.12），覆盖率写进运行摘要 |
| `lint` | 推送、PR | ruff 检查 + 格式、basedpyright（只拦新增） |
| `security` | 推送、PR、每周一 | `pip-audit` 查基础依赖和 dev 依赖的已知漏洞；PR 上 `dependency-review` 拦新加 / 升级的有漏洞依赖（中危及以上） |
| `codeql` | 推 main、PR、每周一 | CodeQL 扫 Python、桌面程序的 TS / JS 和工作流本身，结果在 Security → Code scanning |
| `engine-wheels` | 引擎源码在 main 上变了 | 编四个平台的引擎 wheel 发 Release |

- **actions 一律钉提交 SHA**，后面注释写版本，例如 `actions/checkout@fbc6…  # v5.1.0`。不要写 `@v5` 这种标签：标签能被改指向别的代码。
- **Dependabot**（`.github/dependabot.yml`）每周提升级 PR：actions、pip、`app/` 的 Go 依赖（桌面程序是纯 Go 的 mygo 原生 ui，没有 npm / bun 依赖）。同一生态的小版本合成一个 PR。`vendor/` 不管，跟着上游走。
- **pip 缓存**：各工作流的 `setup-python` 按 `pyproject.toml` 缓存下载的包，依赖没变时装得快。
- `pip-audit` 只查 `.[dev]` 和基础依赖；`play` / `bench` 里有 git 依赖和 KAA 子模块，CI 不装。
