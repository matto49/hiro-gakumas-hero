package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// fixPath：从 Finder / 程序坞打开的 Mac 程序只拿到 /usr/bin:/bin:… 这样的 PATH，找不到用户装的 codex、claude、git。
// 启动时问一次用户的登录 shell 的 PATH（最多 3 秒），再补上常见目录，合并进本进程的 PATH。Windows 的图形程序本来就继承用户 PATH，不用做。
func fixPath() {
	if runtime.GOOS == "windows" {
		return
	}
	extra := loginShellPath()
	home, _ := os.UserHomeDir()
	for _, d := range []string{"/opt/homebrew/bin", "/usr/local/bin", filepath.Join(home, ".local", "bin"), filepath.Join(home, ".bun", "bin")} {
		extra += string(os.PathListSeparator) + d
	}
	_ = os.Setenv("PATH", mergePath(os.Getenv("PATH"), extra))
}

func loginShellPath() string {
	sh := os.Getenv("SHELL")
	if sh == "" {
		sh = "/bin/zsh"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, sh, "-ilc", `printf '%s' "$PATH"`).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// mergePath 把 extra 里还没有的目录接在 base 后面（保持顺序，去掉重复和空项）。
func mergePath(base, extra string) string {
	sep := string(os.PathListSeparator)
	seen := map[string]bool{}
	var out []string
	for _, d := range append(strings.Split(base, sep), strings.Split(extra, sep)...) {
		if d == "" || seen[d] {
			continue
		}
		seen[d] = true
		out = append(out, d)
	}
	return strings.Join(out, sep)
}
