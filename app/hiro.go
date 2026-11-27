package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
)

// Hiro 调工具层的 hiro 命令行：界面和内置 agent 都只通过它干活（设计稿 §4）。
type Hiro struct {
	// Command 是 hiro 命令本身（不含子命令），例如 ["/…/venv/bin/python", "-m", "hiro.cli.main"]；空的时候按 ResolveHiroCommand 找。
	Command []string `json:"-"`
	// Env 只在 hiro confirm（真正开局）时加进环境：对局用的模型设置，含 DeepSeek Key。别的调用不带，减少 Key 出现的地方。
	Env func() []string `json:"-"`
	mu  sync.Mutex
}

// 信封类型在 hiroapi 包（界面子包也用）；这里留别名，老代码不用改。
type (
	CliError = hiroapi.CliError
	Pending  = hiroapi.Pending
	Result   = hiroapi.Result
)

// PendingExit 是待确认的退出码（hiro.cli.main.PENDING_EXIT）。
const PendingExit = hiroapi.PendingExit

// ResolveHiroCommand 找 hiro 命令：HIRO_CLI（JSON 数组）> 程序自己装的环境（HIRO_HOME/src/.venv，见 bootstrap.go）> PATH 上的 hiro。
func ResolveHiroCommand() []string {
	if v := os.Getenv("HIRO_CLI"); v != "" {
		var argv []string
		if json.Unmarshal([]byte(v), &argv) == nil && len(argv) > 0 {
			return argv
		}
		return strings.Fields(v)
	}
	if py := venvPython(HiroHome(), runtime.GOOS); exists(py) {
		return []string{py, "-m", "hiro.cli.main"}
	}
	return []string{"hiro"}
}

// HiroHome 和 hiro.cli.home.hiro_home 一致：HIRO_HOME 优先，否则系统的应用数据目录下的 hiro-console。
func HiroHome() string {
	if v := os.Getenv("HIRO_HOME"); v != "" {
		return v
	}
	home, _ := os.UserHomeDir()
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "hiro-console")
	case "windows":
		if a := os.Getenv("APPDATA"); a != "" {
			return filepath.Join(a, "hiro-console")
		}
		return filepath.Join(home, "AppData", "Roaming", "hiro-console")
	default:
		if x := os.Getenv("XDG_DATA_HOME"); x != "" {
			return filepath.Join(x, "hiro-console")
		}
		return filepath.Join(home, ".local", "share", "hiro-console")
	}
}

func (h *Hiro) command() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.Command) == 0 {
		h.Command = ResolveHiroCommand()
	}
	return h.Command
}

// reset 让下次调用重新找 hiro（装好新环境之后）。
func (h *Hiro) reset() {
	h.mu.Lock()
	h.Command = nil
	h.mu.Unlock()
}

// Call 运行一条 hiro 命令，例如 ["info", "summary"]，返回解析后的信封。不会返回 Go 错误：失败都在 Result.Error 里。
func (h *Hiro) Call(ctx context.Context, args []string) Result {
	if r, ok := h.tryService(ctx, args); ok {
		return r
	}
	return h.run(ctx, args, nil)
}

// SetSecret 保存一个账号或密码：值只经 stdin 交给 hiro secret set，不进命令行参数、不进日志。
func (h *Hiro) SetSecret(ctx context.Context, name string, value string) Result {
	return h.run(ctx, []string{"secret", "set", name}, []byte(value))
}

func (h *Hiro) run(ctx context.Context, args []string, stdin []byte) Result {
	base := h.command()
	cmd := exec.CommandContext(ctx, base[0], append(append([]string{}, base[1:]...), args...)...)
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1")
	if h.Env != nil && len(args) > 0 && args[0] == "confirm" {
		cmd.Env = append(cmd.Env, h.Env()...)
	}
	hideWindow(cmd)
	if stdin != nil {
		cmd.Stdin = bytes.NewReader(stdin)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	exit := 0
	var ee *exec.ExitError
	switch {
	case err == nil:
	case errors.As(err, &ee):
		exit = ee.ExitCode()
	default:
		return Result{Exit: -1, Error: &CliError{Code: "hiro_missing", Message: "找不到 hiro 工具层：" + err.Error(),
			Hint: "打开设置 → 环境，重新安装；开发时设 HIRO_CLI"}}
	}
	// 信封是最后一行 JSON（--follow 之类会先打印事件行）
	lines := strings.Split(strings.TrimSpace(stdout.String()), "\n")
	var r Result
	if len(lines) == 0 || json.Unmarshal([]byte(lines[len(lines)-1]), &r) != nil {
		msg := strings.TrimSpace(stderr.String())
		if msg == "" {
			msg = strings.TrimSpace(stdout.String())
		}
		return Result{Exit: exit, Error: &CliError{Code: "bad_output", Message: badOutputMessage(msg),
			Hint: "运行 hiro doctor 检查环境"}}
	}
	r.Exit = exit
	if r.OK && exit == PendingExit {
		var d struct {
			Pending *Pending `json:"pending"`
		}
		if json.Unmarshal(r.Data, &d) == nil {
			r.Pending = d.Pending
		}
	}
	return r
}

// badOutputMessage 是 hiro 没给出信封时界面上显示的一句话。Python 的 traceback 整段丢给用户没法看（N46：用量页满屏 traceback），
// 只留最后一行异常；不是 traceback 的原样给。
func badOutputMessage(msg string) string {
	if strings.Contains(msg, "Traceback (most recent call last):") {
		lines := strings.Split(strings.TrimSpace(msg), "\n")
		last := strings.TrimSpace(lines[len(lines)-1])
		if last != "" {
			return "hiro 工具层出错了：" + last
		}
	}
	if len(msg) > 2000 { // 先认 traceback 再截断（#174 review：长 traceback 截掉开头那行就认不出了）
		msg = msg[len(msg)-2000:]
	}
	return "hiro 工具层输出看不懂：" + msg
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
