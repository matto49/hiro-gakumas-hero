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
)

// Hiro 调工具层的 hiro 命令行：界面和内置 agent 都只通过它干活（设计稿 §4）。
type Hiro struct {
	// Command 是 hiro 命令本身（不含子命令），例如 ["/…/venv/bin/python", "-m", "hiro.cli.main"]。
	Command []string `json:"-"`
}

// CliError 是命令失败时信封里的 error：code 给程序判断，message / hint 是写给人看的白话。
type CliError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Hint    string `json:"hint,omitempty"`
}

// Pending 是会动游戏的命令返回的待确认：用户点了确认卡才 confirm。
type Pending struct {
	ID        string  `json:"id"`
	Command   string  `json:"command"`
	Summary   string  `json:"summary"`
	ExpiresAt float64 `json:"expires_at"`
}

// Result 是一次命令的结果：信封原样带回，再加上退出码和解析出的待确认。
type Result struct {
	OK      bool            `json:"ok"`
	Data    json.RawMessage `json:"data,omitempty"`
	Error   *CliError       `json:"error,omitempty"`
	Pending *Pending        `json:"pending,omitempty"`
	Exit    int             `json:"exit"`
}

// PendingExit 是待确认的退出码（hiro.cli.main.PENDING_EXIT）。
const PendingExit = 3

// ResolveHiroCommand 找 hiro 命令：HIRO_CLI（JSON 数组）> 程序自己的 Python 环境（向导装在 HIRO_HOME/venv）> PATH 上的 hiro。
func ResolveHiroCommand() []string {
	if v := os.Getenv("HIRO_CLI"); v != "" {
		var argv []string
		if json.Unmarshal([]byte(v), &argv) == nil && len(argv) > 0 {
			return argv
		}
		return strings.Fields(v)
	}
	py := filepath.Join(HiroHome(), "venv", "bin", "python")
	if runtime.GOOS == "windows" {
		py = filepath.Join(HiroHome(), "venv", "Scripts", "python.exe")
	}
	if _, err := os.Stat(py); err == nil {
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
	if len(h.Command) == 0 {
		h.Command = ResolveHiroCommand()
	}
	return h.Command
}

// Call 运行一条 hiro 命令，例如 ["info", "summary"]，返回解析后的信封。不会返回 Go 错误：失败都在 Result.Error 里。
func (h *Hiro) Call(ctx context.Context, args []string) Result {
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
		if len(msg) > 2000 {
			msg = msg[len(msg)-2000:]
		}
		return Result{Exit: exit, Error: &CliError{Code: "bad_output", Message: "hiro 工具层输出看不懂：" + msg,
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

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
