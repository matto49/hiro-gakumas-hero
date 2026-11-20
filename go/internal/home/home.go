// Package home 是初星助手的数据目录 HIRO_HOME（信息库、资料库、任务、待确认），和 Python 版 hiro.cli.home 同一个位置。
package home

import (
	"os"
	"path/filepath"
	"runtime"
)

// AppDir 是数据目录名。
const AppDir = "hiro-console"

// Dir 返回 HIRO_HOME：环境变量优先；缺省 Mac ~/Library/Application Support/hiro-console，
// Windows %APPDATA%\hiro-console，其他 $XDG_DATA_HOME 或 ~/.local/share 下的 hiro-console。
func Dir() string {
	if v := os.Getenv("HIRO_HOME"); v != "" {
		return v
	}
	h, _ := os.UserHomeDir()
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(h, "Library", "Application Support", AppDir)
	case "windows":
		if a := os.Getenv("APPDATA"); a != "" {
			return filepath.Join(a, AppDir)
		}
		return filepath.Join(h, "AppData", "Roaming", AppDir)
	default:
		if x := os.Getenv("XDG_DATA_HOME"); x != "" {
			return filepath.Join(x, AppDir)
		}
		return filepath.Join(h, ".local", "share", AppDir)
	}
}

// Ensure 返回 HIRO_HOME，并保证目录存在。
func Ensure() (string, error) {
	d := Dir()
	return d, os.MkdirAll(d, 0o750)
}

// Sub 返回 HIRO_HOME 下的子目录，并保证它存在。
func Sub(name ...string) (string, error) {
	h, err := Ensure()
	if err != nil {
		return "", err
	}
	d := filepath.Join(append([]string{h}, name...)...)
	return d, os.MkdirAll(d, 0o750)
}
