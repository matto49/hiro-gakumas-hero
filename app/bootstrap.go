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
	"sync"
	"time"

	"github.com/egoist/mygo"
)

// 第一次打开时装 Python 环境（设计稿 §4、§11 第 1 步）：程序自带 uv（resources/<平台>/bin/uv），
// 把 hiro-gakumas-hero 拉到 HIRO_HOME/src，再跑仓库自己的安装脚本（scripts/setup.sh / setup.ps1：Python 3.10 → 依赖 → 游戏数据 → 自检）。
// 用户不用装 Python。国内网络连不上 PyPI 时自动换镜像。

// Step 是安装的一步。
type Step struct {
	ID    string   `json:"id"`
	Title string   `json:"title"`
	Argv  []string `json:"-"`
	Dir   string   `json:"-"`
	Env   []string `json:"-"`
}

// BootEnv 是做安装计划需要知道的事（测试里换成假的）。
type BootEnv struct {
	Home, Resources, GOOS string
	RepoURL, Ref          string
	MirrorCN              bool
	LookPath              func(string) (string, error)
	Exists                func(string) bool
	FreeBytes             func(dir string) (uint64, error) // nil：不查
}

// needFree 是装环境要的磁盘空间（Python、依赖、游戏数据、下载缓存；2026-11-17 实测装到一半已占 2.5 GB）。
const needFree = 6 << 30

// installedMarker 是安装脚本整个跑完后写的标记：setup.sh 第 2 步就建好了 .venv，只看 .venv 会把装了一半当成装好了。
const installedMarker = ".hiro-installed"

const (
	defaultRepoURL = "https://github.com/matto49/hiro-gakumas-hero"
	defaultRepoRef = "main"
	mirrorIndex    = "https://pypi.tuna.tsinghua.edu.cn/simple"
	mirrorPython   = "https://registry.npmmirror.com/-/binary/python-build-standalone"
)

func venvPython(home, goos string) string {
	if goos == "windows" {
		return filepath.Join(home, "src", ".venv", "Scripts", "python.exe")
	}
	return filepath.Join(home, "src", ".venv", "bin", "python")
}

// PlanBootstrap 算出还差哪几步；已经装好时返回空。缺 git / uv 时返回带白话提示的错误。
func PlanBootstrap(e BootEnv) ([]Step, *CliError) {
	src := filepath.Join(e.Home, "src")
	if e.Exists(filepath.Join(src, ".git")) && e.Exists(venvPython(e.Home, e.GOOS)) && e.Exists(filepath.Join(src, installedMarker)) {
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
	git, err := e.LookPath("git")
	if err != nil {
		hint := "装好 Git 后再点「安装」：https://git-scm.com/download/win"
		if e.GOOS == "darwin" {
			hint = "打开「终端」运行 xcode-select --install，装好后再点「安装」"
		}
		return nil, &CliError{Code: "git_missing", Message: "这台电脑没有 git（下载初星助手的程序要用）", Hint: hint}
	}
	if e.FreeBytes != nil {
		if free, err := e.FreeBytes(e.Home); err == nil && free < needFree {
			return nil, &CliError{Code: "disk_full", Message: fmt.Sprintf("磁盘只剩 %.1f GB，装环境要 %d GB 左右", float64(free)/(1<<30), needFree>>30),
				Hint: "清掉一些大文件（下载、录屏、旧安装包）后再点「安装」"}
		}
	}
	var steps []Step
	if !e.Exists(filepath.Join(src, ".git")) {
		repo, ref := e.RepoURL, e.Ref
		if repo == "" {
			repo = defaultRepoURL
		}
		if ref == "" {
			ref = defaultRepoRef
		}
		steps = append(steps, Step{ID: "source", Title: "下载初星助手的程序", Argv: []string{git, "clone", "--depth", "1", "--branch", ref, repo, src}, Dir: e.Home})
	}
	env := []string{"PATH=" + uvDir + string(os.PathListSeparator) + os.Getenv("PATH"), "PYTHONUTF8=1"}
	if e.MirrorCN {
		env = append(env, "UV_INDEX_URL="+mirrorIndex, "UV_PYTHON_INSTALL_MIRROR="+mirrorPython)
	}
	setup := Step{ID: "setup", Title: "安装 Python、依赖和游戏数据（约 5 分钟）", Dir: src, Env: env,
		Argv: []string{"bash", filepath.Join(src, "scripts", "setup.sh")}}
	if e.GOOS == "windows" {
		setup.Argv = []string{"powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", filepath.Join(src, "scripts", "setup.ps1")}
	}
	return append(steps, setup), nil
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
	e := BootEnv{Home: b.Home, Resources: res, GOOS: runtime.GOOS, RepoURL: os.Getenv("HIRO_REPO_URL"), Ref: os.Getenv("HIRO_REPO_REF"),
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
	if !b.mu.TryLock() {
		return errors.New("已经在安装了")
	}
	defer b.mu.Unlock()
	if err := os.MkdirAll(b.Home, 0o755); err != nil {
		return err
	}
	steps, perr := PlanBootstrap(b.env(ctx, true))
	if perr != nil {
		return errors.New(perr.Message + "。" + perr.Hint)
	}
	for _, st := range steps {
		_ = ch.Send(Progress{Step: st.ID, Line: "==> " + st.Title})
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
			return errors.New(st.Title + "没能开始：" + err.Error())
		}
		sc := bufio.NewScanner(out)
		sc.Buffer(make([]byte, 64*1024), 1<<20)
		for sc.Scan() {
			_ = ch.Send(Progress{Step: st.ID, Line: sc.Text()})
		}
		if err := cmd.Wait(); err != nil {
			return errors.New(st.Title + "失败：" + err.Error() + "（看上面的输出；网络问题可以稍后重试）")
		}
	}
	if err := os.WriteFile(filepath.Join(b.Home, "src", installedMarker), []byte(time.Now().Format(time.RFC3339)+"\n"), 0o644); err != nil {
		return err
	}
	b.Hiro.reset() // 下次调用重新找 hiro（改用新装的 Python）
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
