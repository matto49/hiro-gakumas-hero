// Package pybridge 在迁移期间把还没迁到 Go 的部分交给 Python：找 Python 和仓库，调 `python -m hiro.cli.main …`。
// 迁完一块删一块；阶段 6 整个包删掉（go/README.md「迁移期间怎么共存」）。
package pybridge

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

// Timeout 是一条 Python 命令最长跑多久（出编成方案、生成复盘页都在几十秒内）。
var Timeout = 5 * time.Minute

func isRepo(dir string) bool {
	_, err := os.Stat(filepath.Join(dir, "src", "hiro", "__init__.py"))
	return err == nil
}

func walkUp(dir string) string {
	for d := dir; ; {
		if isRepo(d) {
			return d
		}
		parent := filepath.Dir(d)
		if parent == d {
			return ""
		}
		d = parent
	}
}

// Repo 返回 hiro-gakumas-hero 仓库根：HIRO_REPO → 从程序所在目录、当前目录往上找 src/hiro → HIRO_HOME/src（程序自己装的）。
func Repo() (string, error) {
	if v := os.Getenv("HIRO_REPO"); v != "" {
		if !isRepo(v) {
			return "", clierr.New("repo_missing", "HIRO_REPO 指的目录不是 hiro-gakumas-hero 仓库："+v, "改成仓库根目录（里面有 src/hiro）")
		}
		return v, nil
	}
	var starts []string
	if exe, err := os.Executable(); err == nil {
		if real, err := filepath.EvalSymlinks(exe); err == nil {
			exe = real
		}
		starts = append(starts, filepath.Dir(exe))
	}
	if wd, err := os.Getwd(); err == nil {
		starts = append(starts, wd)
	}
	for _, s := range starts {
		if r := walkUp(s); r != "" {
			return r, nil
		}
	}
	if src := filepath.Join(home.Dir(), "src"); isRepo(src) {
		return src, nil
	}
	return "", clierr.New("repo_missing", "找不到初星助手的 Python 程序（hiro-gakumas-hero）", "重新安装（设置 → 环境 → 重新安装），或设 HIRO_REPO 指向仓库")
}

func venvPython(root string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(root, ".venv", "Scripts", "python.exe")
	}
	return filepath.Join(root, ".venv", "bin", "python")
}

func executable(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// Python 返回跑对局和还没迁的命令用的 Python：HIRO_PYTHON → 仓库的 .venv → HIRO_HOME/src/.venv。
func Python() (string, error) {
	if v := os.Getenv("HIRO_PYTHON"); v != "" {
		if !executable(v) {
			return "", clierr.New("python_missing", "HIRO_PYTHON 指的 Python 不存在："+v, "改成存在的 Python，或去掉这个环境变量")
		}
		return v, nil
	}
	var cands []string
	if r, err := Repo(); err == nil {
		cands = append(cands, venvPython(r))
	}
	cands = append(cands, venvPython(filepath.Join(home.Dir(), "src")))
	for _, c := range cands {
		if executable(c) {
			return c, nil
		}
	}
	return "", clierr.New("python_missing", "找不到初星助手的 Python 环境", "重新安装（设置 → 环境 → 重新安装），或设 HIRO_PYTHON")
}

// Env 是跑 Python 子进程的环境：当前环境 + PYTHONPATH 把仓库 src 放最前 + 输出按 UTF-8、不缓冲。
func Env(repo string) []string {
	src := filepath.Join(repo, "src")
	pp := src
	if old := os.Getenv("PYTHONPATH"); old != "" {
		pp = src + string(os.PathListSeparator) + old
	}
	env := []string{}
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "PYTHONPATH=") {
			env = append(env, kv)
		}
	}
	return append(env, "PYTHONPATH="+pp, "PYTHONUTF8=1", "PYTHONUNBUFFERED=1")
}

// CLI 用 Python 版跑一条 hiro 命令，返回信封里的 data；失败转成 *clierr.Error（退出码照搬）。
func CLI(argv ...string) (any, error) {
	repo, err := Repo()
	if err != nil {
		return nil, err
	}
	py, err := Python()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), Timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, py, append([]string{"-m", "hiro.cli.main"}, argv...)...)
	cmd.Env = Env(repo)
	cmd.Dir = repo
	proc.Hide(cmd)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	runErr := cmd.Run()
	exit := 0
	var ee *exec.ExitError
	if errors.As(runErr, &ee) {
		exit = ee.ExitCode()
	} else if runErr != nil {
		return nil, clierr.New("python_failed", fmt.Sprintf("Python 没能运行：%v", runErr), "重新安装（设置 → 环境 → 重新安装）")
	}
	env := lastJSONLine(stdout.Bytes())
	if env == nil {
		tail := strings.TrimSpace(stderr.String())
		if len(tail) > 600 {
			tail = "…" + tail[len(tail)-600:]
		}
		return nil, clierr.New("python_failed", fmt.Sprintf("Python 版 hiro %s 没有输出结果（退出码 %d）：%s", strings.Join(argv, " "), exit, tail),
			"运行 hiro doctor 检查环境")
	}
	if ok, _ := env.Get("ok"); ok == true {
		v, _ := env.Get("data")
		return v, nil
	}
	e := jsonx.Obj(env, "error")
	if exit == 0 {
		exit = 1
	}
	return nil, &clierr.Error{Code: jsonx.Str(e, "code"), Message: jsonx.Str(e, "message"), Hint: jsonx.Str(e, "hint"), Exit: exit}
}

// lastJSONLine 取输出里最后一行 JSON 对象（前面可能有日志）。
func lastJSONLine(out []byte) *jsonx.Object {
	lines := bytes.Split(bytes.TrimSpace(out), []byte("\n"))
	for i := len(lines) - 1; i >= 0; i-- {
		if o, err := jsonx.DecodeObject(bytes.TrimSpace(lines[i])); err == nil && o.Has("ok") {
			return o
		}
	}
	return nil
}
