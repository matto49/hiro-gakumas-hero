package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeFile(t *testing.T, p, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func oldInstall(t *testing.T, home, ref string) string {
	t.Helper()
	src := filepath.Join(home, "src")
	writeFile(t, filepath.Join(src, sourceInfoFile), `{"source": "tag", "ref": "`+ref+`", "commit": "aaa"}`)
	writeFile(t, filepath.Join(src, "scripts", "setup.sh"), "old")
	writeFile(t, filepath.Join(src, installedMarker), "x")
	writeFile(t, filepath.Join(src, ".venv", "bin", "python"), "venv")
	writeFile(t, filepath.Join(src, "resources", "game.db"), "res")
	writeFile(t, filepath.Join(src, "cache", "master", "game.db"), "cache")
	writeFile(t, filepath.Join(src, "vendor", "gakumas-player", "data", "master", "current.json"), "master")
	writeFile(t, filepath.Join(src, "src", "hiro", "old.py"), "old code")
	return src
}

func TestSourceBehindOnlyForTagInstallsOfAnotherVersion(t *testing.T) {
	home := t.TempDir()
	if _, _, behind := SourceBehind(home, "0.1.1"); behind {
		t.Fatal("没装源码时不该提示")
	}
	oldInstall(t, home, "desktop-v0.1.1-rc.9")
	have, want, behind := SourceBehind(home, "0.1.1-rc.11")
	if !behind || have != "0.1.1-rc.9" || want != "desktop-v0.1.1-rc.11" {
		t.Fatalf("got %q %q %v", have, want, behind)
	}
	if _, _, behind := SourceBehind(home, "0.1.1-rc.9"); behind {
		t.Fatal("同版本不该提示")
	}
	if _, _, behind := SourceBehind(home, ""); behind {
		t.Fatal("没有版本号（开发构建）不该提示")
	}
	for _, kind := range []string{"main", "ref"} {
		h := t.TempDir()
		writeFile(t, filepath.Join(h, "src", sourceInfoFile), `{"source": "`+kind+`", "ref": "main"}`)
		if _, _, behind := SourceBehind(h, "0.1.1"); behind {
			t.Fatalf("source=%s 不该提示（开发版 / 指定分支）", kind)
		}
	}
}

func TestSwapSourceKeepsVenvAndDataAndRemovesTheOldCopy(t *testing.T) {
	home := t.TempDir()
	oldInstall(t, home, "desktop-v0.1.1-rc.9")
	var installedIn string
	err := swapSource(context.Background(), swapSpec{
		Home: home,
		Fetch: func(ctx context.Context, dest string, say func(string)) error {
			writeFile(t, filepath.Join(dest, sourceInfoFile), `{"source": "tag", "ref": "desktop-v0.1.1"}`)
			writeFile(t, filepath.Join(dest, "scripts", "setup.sh"), "new")
			writeFile(t, filepath.Join(dest, "src", "hiro", "new.py"), "new code")
			return nil
		},
		Install: func(ctx context.Context, say func(string)) error {
			b, _ := os.ReadFile(filepath.Join(home, "src", ".venv", "bin", "python"))
			installedIn = string(b)
			return nil
		},
	}, func(string) {})
	if err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(home, "src")
	if installedIn != "venv" {
		t.Fatal("安装脚本跑的时候 .venv 应该已经搬进新的 src")
	}
	for p, want := range map[string]string{
		"scripts/setup.sh": "new", "src/hiro/new.py": "new code", ".venv/bin/python": "venv",
		"resources/game.db": "res", "cache/master/game.db": "cache", "vendor/gakumas-player/data/master/current.json": "master",
	} {
		b, err := os.ReadFile(filepath.Join(src, p))
		if err != nil || string(b) != want {
			t.Fatalf("%s = %q, %v", p, b, err)
		}
	}
	if _, err := os.Stat(filepath.Join(src, "src", "hiro", "old.py")); err == nil {
		t.Fatal("旧代码不该留在新的 src 里")
	}
	if _, err := os.Stat(filepath.Join(src, installedMarker)); err != nil {
		t.Fatal("装好后要写装完标记")
	}
	ents, _ := os.ReadDir(home)
	for _, e := range ents {
		if strings.HasPrefix(e.Name(), "src.old-") {
			t.Fatal("成功后旧的 src 要删掉")
		}
	}
}

func TestSwapSourceRestoresTheOldCopyWhenDownloadOrInstallFails(t *testing.T) {
	for _, failAt := range []string{"fetch", "install"} {
		home := t.TempDir()
		oldInstall(t, home, "desktop-v0.1.1-rc.9")
		err := swapSource(context.Background(), swapSpec{
			Home: home,
			Fetch: func(ctx context.Context, dest string, say func(string)) error {
				if failAt == "fetch" {
					return errors.New("网断了")
				}
				writeFile(t, filepath.Join(dest, "scripts", "setup.sh"), "new")
				return nil
			},
			Install: func(ctx context.Context, say func(string)) error { return errors.New("装失败") },
		}, func(string) {})
		if err == nil {
			t.Fatalf("%s 失败时应该报错", failAt)
		}
		src := filepath.Join(home, "src")
		for p, want := range map[string]string{"scripts/setup.sh": "old", ".venv/bin/python": "venv", "src/hiro/old.py": "old code", installedMarker: "x"} {
			b, err := os.ReadFile(filepath.Join(src, p))
			if err != nil || string(b) != want {
				t.Fatalf("%s 失败后 %s = %q, %v（应恢复旧的）", failAt, p, b, err)
			}
		}
		ents, _ := os.ReadDir(home)
		for _, e := range ents {
			if e.Name() != "src" {
				t.Fatalf("%s 失败后 Home 里留下了 %s", failAt, e.Name())
			}
		}
	}
}
