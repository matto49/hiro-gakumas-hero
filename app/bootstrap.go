package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo"
)

// 第一次打开时装 Python 环境（设计稿 §4、§11 第 1 步）：程序自带 uv（resources/<平台>/bin/uv），
// 把和安装包同版本的源码（GitHub 标签 desktop-v<版本> 的压缩包，不用 git）下到 HIRO_HOME/src，再跑仓库自己的安装脚本（scripts/setup.sh / setup.ps1：Python 3.10 → 依赖 → 游戏数据 → 自检）。
// 用户不用装 Python。国内网络连不上 PyPI 时自动换镜像。

// Step 是安装的一步。
type Step struct {
	ID    string   `json:"id"`
	Title string   `json:"title"`
	Argv  []string `json:"-"`
	Dir   string   `json:"-"`
	Env   []string `json:"-"`
	// Run 不为空时这一步是程序自己做的（下载源码），不调外部命令；say 输出一行进度。
	Run func(ctx context.Context, say func(string)) error `json:"-"`
	// Optional 为真的步骤失败只提示、不中断安装（装不上就用退路，比如 SIMD 文字识别退回 RapidOCR）。
	Optional bool `json:"optional,omitempty"`
}

// BootEnv 是做安装计划需要知道的事（测试里换成假的）。
type BootEnv struct {
	Home, Resources, GOOS string
	RepoURL, Ref          string // RepoURL / Ref 来自 HIRO_REPO_URL / HIRO_REPO_REF；Ref 不空就只下这个分支 / 标签
	Version               string // 安装包自己的版本（mygo.json 的 version），决定下载哪个标签的源码；空 = 开发版
	HTTP                  *http.Client
	Backoff               time.Duration // 下载重试前的等待（测试里调小）
	MirrorCN              bool
	LookPath              func(string) (string, error)
	Exists                func(string) bool
	FreeBytes             func(dir string) (uint64, error) // nil：不查
}

// needFree 是装环境要的磁盘空间（Python、依赖、游戏数据、下载缓存；2026-11-17 实测装到一半已占 2.5 GB）。
const needFree = 6 << 30

// installedMarker 是安装脚本整个跑完后写的标记：setup.sh 第 1 步就建好了 .venv，只看 .venv 会把装了一半当成装好了。
const installedMarker = ".hiro-installed"

const (
	defaultRepoURL = "https://github.com/matto49/hiro-gakumas-hero"
	mirrorIndex    = "https://pypi.tuna.tsinghua.edu.cn/simple"
	mirrorPython   = "https://registry.npmmirror.com/-/binary/python-build-standalone"
)

func venvPython(home, goos string) string {
	if goos == "windows" {
		return filepath.Join(home, "src", ".venv", "Scripts", "python.exe")
	}
	return filepath.Join(home, "src", ".venv", "bin", "python")
}

// PlanBootstrap 算出还差哪几步；已经装好时返回空。缺 uv / 磁盘不够时返回带白话提示的错误。
func PlanBootstrap(e BootEnv) ([]Step, *CliError) {
	src := filepath.Join(e.Home, "src")
	// 装好的判断只看 venv 的 python 和装完标记，不看 .git：新装的源码是压缩包解出来的没有 .git，以前 git clone 的旧安装照样认
	if e.Exists(venvPython(e.Home, e.GOOS)) && e.Exists(filepath.Join(src, installedMarker)) {
		return nil, nil
	}
	uvName := "uv"
	if e.GOOS == "windows" {
		uvName = "uv.exe"
	}
	uvDir := filepath.Join(e.Resources, "bin")
	if !e.Exists(filepath.Join(uvDir, uvName)) {
		p, err := e.LookPath("uv")
		if err != nil {
			return nil, &CliError{Code: "uv_missing", Message: "安装包里少了 uv（装 Python 用的工具）", Hint: "重新下载安装包；开发时先跑 bun run fetch-uv"}
		}
		uvDir = filepath.Dir(p)
	}
	if e.FreeBytes != nil {
		if free, err := e.FreeBytes(e.Home); err == nil && free < needFree {
			return nil, &CliError{Code: "disk_full", Message: fmt.Sprintf("磁盘只剩 %.1f GB，装环境要 %d GB 左右", float64(free)/(1<<30), needFree>>30),
				Hint: "清掉一些大文件（下载、录屏、旧安装包）后再点「安装」"}
		}
	}
	var steps []Step
	// 源码已经在（旧安装的 .git，或新装解出来的安装脚本）就不再下载；半截目录由下载步骤清掉重来
	if !e.Exists(filepath.Join(src, ".git")) && !e.Exists(filepath.Join(src, "scripts", "setup.sh")) {
		spec := SourceSpec{Repo: e.RepoURL, Ref: e.Ref, Dest: src, Client: e.HTTP, Backoff: e.Backoff}
		if spec.Repo == "" {
			spec.Repo = defaultRepoURL
		}
		if v := strings.TrimPrefix(strings.TrimSpace(e.Version), "v"); v != "" {
			spec.Tag = "desktop-v" + v
		}
		steps = append(steps, Step{ID: "source", Title: "下载初星助手的程序", Dir: e.Home,
			Run: func(ctx context.Context, say func(string)) error { return FetchSource(ctx, spec, say) }})
	}
	env := []string{"PATH=" + uvDir + string(os.PathListSeparator) + os.Getenv("PATH"), "PYTHONUTF8=1"}
	if e.MirrorCN {
		env = append(env, "UV_INDEX_URL="+mirrorIndex, "UV_PYTHON_INSTALL_MIRROR="+mirrorPython)
	}
	setup := Step{ID: "setup", Title: "安装 Python、依赖和游戏数据（约 5 分钟）", Dir: src, Env: env,
		Argv: []string{"bash", filepath.Join(src, "scripts", "setup.sh")}}
	steps = append(steps, setup)
	if e.GOOS == "windows" {
		// SIMD 文字识别（Windows 缺省的识别后端）单独成一步：setup.ps1 里的同一项用 -NoSimd 跳过。可选，失败不挡安装，识别退回 RapidOCR；
		// 脚本只用标准库，装辅助程序 + 模型（GitHub Release ocr-simd-v1）和没有就装的 .NET 10 Runtime，都在 src/.deps 下，不要管理员权限
		steps[len(steps)-1].Argv = []string{"powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", filepath.Join(src, "scripts", "setup.ps1"), "-NoSimd"}
		steps = append(steps, Step{ID: "simd", Title: "安装 SIMD 文字识别（可选，约 100 MB；装不上就用 RapidOCR）", Dir: src, Env: env, Optional: true,
			Argv: []string{venvPython(e.Home, e.GOOS), filepath.Join(src, "tools", "assets", "simd_ocr.py")}})
	}
	return steps, nil
}

// needMirror：3 秒内连不上 PyPI 就用国内镜像。
func needMirror(ctx context.Context) bool {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "HEAD", "https://pypi.org/simple/", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return true
	}
	resp.Body.Close()
	return resp.StatusCode >= 400
}

// ---------------------------------------------------------------------------- 绑定给网页

// Progress 是安装过程的一行输出。
type Progress struct {
	Step string `json:"step"`
	Line string `json:"line"`
}

// BootStatus 是安装状态。
type BootStatus struct {
	Ready bool      `json:"ready"`
	Steps []Step    `json:"steps"`
	Error *CliError `json:"error,omitempty"`
}

// Bootstrap 是安装入口（绑定给网页）。
type Bootstrap struct {
	Home string `json:"-"`
	Hiro *Hiro  `json:"-"`
	mu   sync.Mutex
}

func (b *Bootstrap) env(ctx context.Context, probeMirror bool) BootEnv {
	res, _ := mygo.App.Path(mygo.PathResources)
	e := BootEnv{Home: b.Home, Resources: res, Version: mygo.App.Version(), GOOS: runtime.GOOS, RepoURL: os.Getenv("HIRO_REPO_URL"), Ref: os.Getenv("HIRO_REPO_REF"),
		LookPath: exec.LookPath, Exists: func(p string) bool { _, err := os.Stat(p); return err == nil }, FreeBytes: freeBytes}
	if probeMirror {
		e.MirrorCN = needMirror(ctx)
	}
	return e
}

// Status 看还差哪几步。开发时设了 HIRO_CLI 就当已经装好。
func (b *Bootstrap) Status(ctx context.Context) BootStatus {
	if os.Getenv("HIRO_CLI") != "" {
		return BootStatus{Ready: true, Steps: []Step{}}
	}
	steps, err := PlanBootstrap(b.env(ctx, false))
	if steps == nil {
		steps = []Step{}
	}
	return BootStatus{Ready: err == nil && len(steps) == 0, Steps: steps, Error: err}
}

// Run 一步步装，每行输出经 ch 送给网页。装好后让 Hiro 改用新装的 Python。
func (b *Bootstrap) Run(ctx context.Context, ch *mygo.Channel[Progress]) error {
	return b.RunLines(ctx, func(p Progress) { _ = ch.Send(p) })
}

// RunLines 同 Run，每行输出交给 send（原生界面用）。
func (b *Bootstrap) RunLines(ctx context.Context, send func(Progress)) error {
	if !b.mu.TryLock() {
		return errors.New("已经在安装了")
	}
	defer b.mu.Unlock()
	if os.Getenv("HIRO_CLI") != "" { // 开发环境用自己的 hiro 命令，不下载也不装（和 Status 一致）
		return nil
	}
	if err := os.MkdirAll(b.Home, 0o755); err != nil {
		return err
	}
	steps, perr := PlanBootstrap(b.env(ctx, true))
	if perr != nil {
		return errors.New(perr.Message + "。" + perr.Hint)
	}
	if err := runSteps(ctx, steps, send); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(b.Home, "src", installedMarker), []byte(time.Now().Format(time.RFC3339)+"\n"), 0o644); err != nil {
		return err
	}
	b.Hiro.reset() // 下次调用重新找 hiro（改用新装的 Python）
	return nil
}

// runSteps 一步步做；必做的步骤失败就停，Optional 的只提示。
func runSteps(ctx context.Context, steps []Step, send func(Progress)) error {
	for _, st := range steps {
		send(Progress{Step: st.ID, Line: "==> " + st.Title})
		// 可选步骤没做成：只提示，接着装后面的（设置 → 运行环境 →「重装运行环境」会再试一次）
		failed := func(err error) error {
			if st.Optional && ctx.Err() == nil {
				send(Progress{Step: st.ID, Line: "⚠ " + st.Title + "没做成（" + err.Error() + "），不影响使用，稍后可以在设置里「重装运行环境」再试"})
				return nil
			}
			return err
		}
		if st.Run != nil {
			if err := st.Run(ctx, func(line string) { send(Progress{Step: st.ID, Line: line}) }); err != nil {
				if err = failed(errors.New(st.Title + "失败：" + err.Error() + "（网络问题可以稍后重试）")); err != nil {
					return err
				}
			}
			continue
		}
		cmd := exec.CommandContext(ctx, st.Argv[0], st.Argv[1:]...)
		cmd.Dir = st.Dir
		cmd.Env = append(os.Environ(), st.Env...)
		hideWindow(cmd)
		out, err := cmd.StdoutPipe()
		if err != nil {
			return err
		}
		cmd.Stderr = cmd.Stdout
		if err := cmd.Start(); err != nil {
			if err = failed(errors.New(st.Title + "没能开始：" + err.Error())); err != nil {
				return err
			}
			continue
		}
		sc := bufio.NewScanner(out)
		sc.Buffer(make([]byte, 64*1024), 1<<20)
		for sc.Scan() {
			send(Progress{Step: st.ID, Line: sc.Text()})
		}
		if err := cmd.Wait(); err != nil {
			if err = failed(errors.New(st.Title + "失败：" + err.Error() + "（看上面的输出；网络问题可以稍后重试）")); err != nil {
				return err
			}
		}
	}
	return nil
}

// Reset 去掉「装完」标记：下次「安装」会重跑安装脚本（已有的步骤脚本自己会跳过）。设置 → 环境 → 重新安装用。
func (b *Bootstrap) Reset() error {
	err := os.Remove(filepath.Join(b.Home, "src", installedMarker))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
