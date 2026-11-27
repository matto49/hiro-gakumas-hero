package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// 装新包后换成同版本的 Python 程序（G5，11-28）：安装包升级了，HIRO_HOME/src 还是第一次装环境时下的那一份，
// 新界面会调用旧程序没有的命令 / 参数（开局页 --exam-search、设置页 hiro game close），旧用户一开局就报错。
// 这里按版本体系设计（docs/ryugi/specs/2026-11-26-version-system-design.md §3「重装运行环境」）先做最小版：
// 发现版本不一致只提示，玩家点「更新运行环境」才换（缺省行为不变）；P1 以后在这上面接 Release 源码包和校验。

// sourceInfo 是 HIRO_HOME/src/.hiro-source.json 里用到的字段。
type sourceInfo struct {
	Source string `json:"source"`
	Ref    string `json:"ref"`
	Commit string `json:"commit"`
}

func loadSourceInfo(src string) (sourceInfo, bool) {
	var si sourceInfo
	b, err := os.ReadFile(filepath.Join(src, sourceInfoFile))
	if err != nil || json.Unmarshal(b, &si) != nil {
		return si, false
	}
	return si, true
}

// SourceBehind：装的源码是按某个版本标签下的（source=tag），而且不是这个安装包的版本 → 要换。
// have 是装着的版本（去掉 desktop-v 前缀），want 是要下的标签。main（版本还没发时的退路）、ref（HIRO_REPO_REF）、
// 以前 git clone 的旧安装（没有 .hiro-source.json）、没有版本号的开发构建都不提示。
func SourceBehind(home, version string) (have, want string, behind bool) {
	v := strings.TrimPrefix(strings.TrimSpace(version), "v")
	if v == "" {
		return "", "", false
	}
	si, ok := loadSourceInfo(filepath.Join(home, "src"))
	if !ok || si.Source != "tag" || si.Ref == "" {
		return "", "", false
	}
	want = "desktop-v" + v
	if si.Ref == want {
		return "", "", false
	}
	return strings.TrimPrefix(si.Ref, "desktop-v"), want, true
}

// keepDirs 是换源码时从旧 src 搬到新 src 的目录（装好的 Python、游戏数据、缓存、模拟器主数据、SIMD 依赖），不重下。
var keepDirs = []string{".venv", "resources", "cache", filepath.Join("vendor", "gakumas-player", "data", "master"), ".deps"}

type swapSpec struct {
	Home    string
	Fetch   func(ctx context.Context, dest string, say func(string)) error // 把新源码下载解压到 dest（dest 不存在）
	Install func(ctx context.Context, say func(string)) error              // 在新 src 里跑安装脚本
	Now     func() time.Time
}

// swapSource 把 HIRO_HOME/src 换成新下载的源码：旧的先改名成 src.old-<时间>，新的装好后删掉旧的；
// 下载或安装失败就把新的删掉、旧的改回来（搬过去的 .venv 等目录也搬回去），什么都不留。
func swapSource(ctx context.Context, sp swapSpec, say func(string)) (err error) {
	now := time.Now
	if sp.Now != nil {
		now = sp.Now
	}
	src := filepath.Join(sp.Home, "src")
	old := src + ".old-" + now().Format("20060102-150405")
	if err := os.Rename(src, old); err != nil {
		return fmt.Errorf("旧的程序目录挪不开（可能有文件被占用，关掉初星助手和游戏任务后再试）：%w", err)
	}
	var moved []string
	defer func() {
		if err == nil {
			_ = os.RemoveAll(old)
			return
		}
		// 恢复：搬过去的目录搬回旧 src，删掉新的，旧的改回 src
		for _, d := range moved {
			_ = os.MkdirAll(filepath.Dir(filepath.Join(old, d)), 0o755)
			_ = os.Rename(filepath.Join(src, d), filepath.Join(old, d))
		}
		_ = os.RemoveAll(src)
		if rerr := os.Rename(old, src); rerr != nil {
			err = fmt.Errorf("%w；旧程序也没能恢复（在 %s，改名回 src 即可）", err, old)
		}
	}()
	say("下载和界面同版本的程序…")
	if err := sp.Fetch(ctx, src, say); err != nil {
		return err
	}
	for _, d := range keepDirs {
		from, to := filepath.Join(old, d), filepath.Join(src, d)
		if _, e := os.Stat(from); e != nil {
			continue
		}
		if _, e := os.Stat(to); e == nil {
			continue // 新源码自带（比如仓库里跟踪的 resources），用新的
		}
		if e := os.MkdirAll(filepath.Dir(to), 0o755); e != nil {
			return e
		}
		if e := os.Rename(from, to); e != nil {
			return fmt.Errorf("搬 %s 没成功：%w", d, e)
		}
		moved = append(moved, d)
	}
	say("安装依赖（已装的会跳过）…")
	if err := sp.Install(ctx, say); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(src, installedMarker), []byte(now().Format(time.RFC3339)+"\n"), 0o644)
}

var errNotBehind = errors.New("程序已经是和界面同一个版本")

// Upgrade 把运行环境换成和安装包同版本的程序（设置页「更新运行环境」）。调用方先确认没有局在跑；这里先停后台服务。
func (b *Bootstrap) Upgrade(ctx context.Context, send func(Progress)) error {
	if !b.mu.TryLock() {
		return errors.New("已经在安装了")
	}
	defer b.mu.Unlock()
	if os.Getenv("HIRO_CLI") != "" {
		return nil
	}
	e := b.env(ctx, true)
	_, want, behind := SourceBehind(b.Home, e.Version)
	if !behind {
		return errNotBehind
	}
	say := func(line string) { send(Progress{Step: "upgrade", Line: line}) }
	say("停下后台服务…")
	shutdownService(5 * time.Second)
	repo := e.RepoURL
	if repo == "" {
		repo = defaultRepoURL
	}
	err := swapSource(ctx, swapSpec{
		Home: b.Home,
		Fetch: func(ctx context.Context, dest string, say func(string)) error {
			// 只下这个版本的标签，找不到就报错，不退回 main：换成另一份不知道是什么的代码不如不换
			return FetchSource(ctx, SourceSpec{Repo: repo, Tag: want, TagOnly: true, Dest: dest, Client: e.HTTP, Backoff: e.Backoff}, say)
		},
		Install: func(ctx context.Context, say func(string)) error {
			steps, perr := PlanBootstrap(e)
			if perr != nil {
				return errors.New(perr.Message + "。" + perr.Hint)
			}
			return runSteps(ctx, steps, send)
		},
	}, say)
	if err != nil {
		return err
	}
	b.Hiro.reset()
	return nil
}

// behindText 是版本不一致时给玩家看的话（设置页、首页共用）；一致时为空。开发环境（HIRO_CLI）不提示。
func behindText(home, version string) string {
	if os.Getenv("HIRO_CLI") != "" {
		return ""
	}
	have, want, behind := SourceBehind(home, version)
	if !behind {
		return ""
	}
	return fmt.Sprintf("程序还是 %s 版，界面已经是 %s 版：有些新功能会出错。到设置 → 运行环境点「更新运行环境」换成同一个版本（库存、Key、编成方案都不受影响，约几分钟）。",
		have, strings.TrimPrefix(want, "desktop-v"))
}

// runtimeNotice 是首页顶部的提示（程序和界面版本不一致，G5）：读 .hiro-source.json 的结果缓存 5 秒，不每帧读文件。
func runtimeNotice(b *Bootstrap) func() string {
	if b == nil {
		return nil
	}
	var (
		at   time.Time
		text string
	)
	return func() string {
		if time.Since(at) > 5*time.Second {
			at = time.Now()
			text = behindText(b.Home, b.env(context.Background(), false).Version)
		}
		return text
	}
}
