package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
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
		Home: home, Wait: time.Millisecond,
		Fetch: func(ctx context.Context, dest string, say func(string)) error {
			writeFile(t, filepath.Join(dest, sourceInfoFile), `{"source": "tag", "ref": "desktop-v0.1.1"}`)
			// 仓库跟踪的文件：新源码里 master 目录本来就在（#204 评审：不能因此整个跳过旧主数据）
			writeFile(t, filepath.Join(dest, "vendor", "gakumas-player", "data", "master", "abc", "source-files.json"), "tracked")
			writeFile(t, filepath.Join(dest, "resources", "README.md"), "tracked")
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
		"vendor/gakumas-player/data/master/abc/source-files.json": "tracked", "resources/README.md": "tracked",
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
			Home: home, Wait: time.Millisecond,
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
		for p, want := range map[string]string{"scripts/setup.sh": "old", ".venv/bin/python": "venv", "src/hiro/old.py": "old code"} {
			b, err := os.ReadFile(filepath.Join(src, p))
			if err != nil || string(b) != want {
				t.Fatalf("%s 失败后 %s = %q, %v（应恢复旧的）", failAt, p, b, err)
			}
		}
		_, merr := os.Stat(filepath.Join(src, installedMarker))
		if failAt == "fetch" && merr != nil {
			t.Fatal("下载失败时什么都没动过，装完标记应留着")
		}
		if failAt == "install" && merr == nil {
			t.Fatal("安装失败时新版安装脚本可能动过 .venv：去掉装完标记，下次重跑旧版安装")
		}
		ents, _ := os.ReadDir(home)
		for _, e := range ents {
			if e.Name() != "src" {
				t.Fatalf("%s 失败后 Home 里留下了 %s", failAt, e.Name())
			}
		}
	}
}

func TestSwapSourceKeepsBothCopiesWhenAMoveBackFails(t *testing.T) {
	home := t.TempDir()
	oldInstall(t, home, "desktop-v0.1.1-rc.9")
	var stuck atomic.Bool
	err := swapSource(context.Background(), swapSpec{
		Home: home, Tries: 1,
		Rename: func(from, to string) error {
			if stuck.Load() && strings.HasSuffix(from, string(filepath.Separator)+".venv") {
				return errors.New("文件被占用")
			}
			return os.Rename(from, to)
		},
		Fetch: func(ctx context.Context, dest string, say func(string)) error {
			writeFile(t, filepath.Join(dest, "scripts", "setup.sh"), "new")
			return nil
		},
		Install: func(ctx context.Context, say func(string)) error { stuck.Store(true); return errors.New("装失败") },
	}, func(string) {})
	var kb *errKeptBoth
	if !errors.As(err, &kb) {
		t.Fatalf("搬不回时应报「两份都留着」：%v", err)
	}
	// .venv 还在新 src 里，没被删
	if b, e := os.ReadFile(filepath.Join(home, "src", ".venv", "bin", "python")); e != nil || string(b) != "venv" {
		t.Fatalf("搬不回的 .venv 不能删：%q %v", b, e)
	}
	olds, _ := filepath.Glob(filepath.Join(home, oldSrcPrefix+"*"))
	if len(olds) != 1 {
		t.Fatalf("旧程序应原样留着：%v", olds)
	}
	// 下次打开自动恢复：.venv 合并回旧的、旧的改回 src、去掉装完标记
	recoverSwap(home)
	src := filepath.Join(home, "src")
	for p, want := range map[string]string{"scripts/setup.sh": "old", ".venv/bin/python": "venv", "src/hiro/old.py": "old code", "resources/game.db": "res"} {
		if b, e := os.ReadFile(filepath.Join(src, p)); e != nil || string(b) != want {
			t.Fatalf("恢复后 %s = %q, %v", p, b, e)
		}
	}
	if _, e := os.Stat(filepath.Join(src, installedMarker)); e == nil {
		t.Fatal("恢复后要去掉装完标记，下次重跑安装")
	}
	if olds, _ := filepath.Glob(filepath.Join(home, oldSrcPrefix+"*")); len(olds) != 0 {
		t.Fatalf("恢复后不该再留 src.old-*：%v", olds)
	}
}

func TestRecoverSwapDeletesOldCopiesWhenTheNewOneIsInstalled(t *testing.T) {
	home := t.TempDir()
	writeFile(t, filepath.Join(home, "src", installedMarker), "x")
	writeFile(t, filepath.Join(home, oldSrcPrefix+"20261128-040000", "scripts", "setup.sh"), "old")
	recoverSwap(home)
	if olds, _ := filepath.Glob(filepath.Join(home, oldSrcPrefix+"*")); len(olds) != 0 {
		t.Fatalf("新的已装好：旧的要删掉：%v", olds)
	}
	if _, e := os.Stat(filepath.Join(home, "src", installedMarker)); e != nil {
		t.Fatal("装好的新 src 不能动")
	}
}

func TestRecoverSwapRestoresWhenSrcIsMissing(t *testing.T) {
	home := t.TempDir()
	writeFile(t, filepath.Join(home, oldSrcPrefix+"20261128-040000", "scripts", "setup.sh"), "old")
	writeFile(t, filepath.Join(home, oldSrcPrefix+"20261128-040000", installedMarker), "x")
	recoverSwap(home)
	if b, e := os.ReadFile(filepath.Join(home, "src", "scripts", "setup.sh")); e != nil || string(b) != "old" {
		t.Fatalf("下载阶段就关了：旧的要改回 src：%q %v", b, e)
	}
}

func TestHiroRefusesCallsWhileUpgrading(t *testing.T) {
	h := &Hiro{Command: []string{"/bin/false"}}
	h.upgrading.Store(true)
	for _, r := range []Result{h.Call(context.Background(), []string{"run", "status"}), h.SetSecret(context.Background(), "x", "y")} {
		if r.Error == nil || r.Error.Code != "upgrading" {
			t.Fatalf("更新期间应直接回「正在更新运行环境」，不调 hiro、不拉起服务：%+v", r)
		}
	}
}
