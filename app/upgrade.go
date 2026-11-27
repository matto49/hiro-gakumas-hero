package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
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

// keepDirs 是换源码时从旧 src 合并进新 src 的目录（装好的 Python、游戏数据和自建素材、缓存、模拟器主数据、SIMD 依赖），不重下。
// 新源码里已经有的文件用新的（仓库跟踪的），新源码没有的逐项搬过去（#204 评审：仓库跟踪了 data/master/<提交>/source-files.json，
// 整个目录跳过会把本地 200 多 MB 的主数据丢掉；resources 下的自建素材同理）。
var keepDirs = []string{".venv", "resources", "cache", filepath.Join("vendor", "gakumas-player", "data", "master"), ".deps"}

const oldSrcPrefix = "src.old-"

type swapSpec struct {
	Home    string
	Fetch   func(ctx context.Context, dest string, say func(string)) error // 把新源码下载解压到 dest（dest 不存在）
	Install func(ctx context.Context, say func(string)) error              // 在新 src 里跑安装脚本
	Now     func() time.Time
	// Rename 是 os.Rename（测试里换成会失败的）；Windows 上文件刚被放开时可能还占着，改名失败退避重试几次。
	Rename func(from, to string) error
	Tries  int
	Wait   time.Duration
}

func (sp swapSpec) rename(from, to string) error {
	rn, tries, wait := sp.Rename, sp.Tries, sp.Wait
	if rn == nil {
		rn = os.Rename
	}
	if tries <= 0 {
		tries = 5
	}
	if wait <= 0 {
		wait = time.Second
	}
	var err error
	for i := 0; i < tries; i++ {
		if err = rn(from, to); err == nil {
			return nil
		}
		if i+1 < tries {
			time.Sleep(wait * time.Duration(i+1))
		}
	}
	return err
}

// mergeMove 把 from 里 to 没有的条目搬过去（两边都是目录就往下合并；两边都有的文件留 to 的），搬过去的相对路径记进 moved（回滚用）。
func mergeMove(sp swapSpec, from, to, rel string, moved *[]string) error {
	fi, err := os.Lstat(from)
	if err != nil {
		return nil
	}
	ti, err := os.Lstat(to)
	if err != nil {
		if e := os.MkdirAll(filepath.Dir(to), 0o755); e != nil {
			return e
		}
		if e := sp.rename(from, to); e != nil {
			return fmt.Errorf("搬 %s 没成功：%w", rel, e)
		}
		*moved = append(*moved, rel)
		return nil
	}
	if !fi.IsDir() || !ti.IsDir() {
		return nil // 两边都有：用新源码的
	}
	ents, err := os.ReadDir(from)
	if err != nil {
		return err
	}
	for _, e := range ents {
		if err := mergeMove(sp, filepath.Join(from, e.Name()), filepath.Join(to, e.Name()), filepath.Join(rel, e.Name()), moved); err != nil {
			return err
		}
	}
	return nil
}

// errKeptBoth：回滚时有东西搬不回去，新旧两份都原样留着（不删任何东西）。
type errKeptBoth struct {
	cause error
	old   string
	stuck []string
}

func (e *errKeptBoth) Error() string {
	return fmt.Sprintf("%v；而且回滚时 %s 没能搬回旧程序，新旧两份都原样留着没删（旧的在 %s）。关掉初星助手再打开会自动再试一次恢复",
		e.cause, strings.Join(e.stuck, "、"), e.old)
}
func (e *errKeptBoth) Unwrap() error { return e.cause }

// swapSource 把 HIRO_HOME/src 换成新下载的源码：旧的先改名成 src.old-<时间>，新的装好后删掉旧的。
// 下载失败：删掉新的、旧的改回 src。安装失败：同上，而且旧 src 去掉装完标记（新版安装脚本可能已经改过 .venv 和缓存，
// 下次打开会重跑一遍旧版的安装脚本，#204 评审）。搬回失败就什么都不删、如实报错。
func swapSource(ctx context.Context, sp swapSpec, say func(string)) (err error) {
	now := time.Now
	if sp.Now != nil {
		now = sp.Now
	}
	src := filepath.Join(sp.Home, "src")
	old := filepath.Join(sp.Home, oldSrcPrefix+now().Format("20060102-150405"))
	if err := sp.rename(src, old); err != nil {
		return fmt.Errorf("旧的程序目录挪不开（有文件被占用；等任务结束、过一会儿再点一次）：%w", err)
	}
	var moved []string
	installing := false
	defer func() {
		if err == nil {
			if rerr := os.RemoveAll(old); rerr != nil {
				log.Printf("更新运行环境：删旧程序 %s 没删干净：%v（下次打开再删）", old, rerr)
			}
			return
		}
		// 回滚：搬过去的倒着搬回旧 src；有一项搬不回就停，什么都不删
		var stuck []string
		for i := len(moved) - 1; i >= 0; i-- {
			d := moved[i]
			_ = os.MkdirAll(filepath.Dir(filepath.Join(old, d)), 0o755)
			if e := sp.rename(filepath.Join(src, d), filepath.Join(old, d)); e != nil {
				stuck = append(stuck, d)
			}
		}
		if len(stuck) > 0 {
			err = &errKeptBoth{cause: err, old: old, stuck: stuck}
			return
		}
		if installing {
			_ = os.Remove(filepath.Join(old, installedMarker))
		}
		if e := os.RemoveAll(src); e != nil {
			err = &errKeptBoth{cause: err, old: old, stuck: []string{"新下载的程序目录"}}
			return
		}
		if e := sp.rename(old, src); e != nil {
			err = &errKeptBoth{cause: err, old: old, stuck: []string{"旧程序目录（改回 src）"}}
		}
	}()
	say("下载和界面同版本的程序…")
	if err := sp.Fetch(ctx, src, say); err != nil {
		return err
	}
	for _, d := range keepDirs {
		if err := mergeMove(sp, filepath.Join(old, d), filepath.Join(src, d), d, &moved); err != nil {
			return err
		}
	}
	installing = true
	say("安装依赖（已装的会跳过）…")
	if err := sp.Install(ctx, say); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(src, installedMarker), []byte(now().Format(time.RFC3339)+"\n"), 0o644)
}

// recoverSwap 收拾上次没做完的「更新运行环境」（中途关窗口、崩溃：swapSource 的回滚没机会跑，#204 评审）：
// 新 src 装好了（有装完标记）就删掉 src.old-*；没装好就把新 src 里搬过去的东西合并回最新的那份旧程序、旧的改回 src，
// 并去掉装完标记（.venv 可能被新版安装脚本动过，下次打开重跑旧版安装）。启动时、建界面之前调用。
func recoverSwap(home string) {
	olds, _ := filepath.Glob(filepath.Join(home, oldSrcPrefix+"*"))
	if len(olds) == 0 {
		return
	}
	sort.Strings(olds)
	src := filepath.Join(home, "src")
	if _, err := os.Stat(filepath.Join(src, installedMarker)); err == nil {
		for _, o := range olds {
			if err := os.RemoveAll(o); err != nil {
				log.Printf("收拾更新运行环境留下的 %s：%v", o, err)
			}
		}
		return
	}
	latest := olds[len(olds)-1]
	sp := swapSpec{Tries: 3, Wait: 500 * time.Millisecond}
	if _, err := os.Stat(src); err == nil {
		var moved []string
		for _, d := range keepDirs {
			if err := mergeMove(sp, filepath.Join(src, d), filepath.Join(latest, d), d, &moved); err != nil {
				log.Printf("恢复上次没更新完的运行环境：%v（两份都留着）", err)
				return
			}
		}
		if err := os.RemoveAll(src); err != nil {
			log.Printf("恢复上次没更新完的运行环境：删新 src 失败 %v（两份都留着）", err)
			return
		}
	}
	_ = os.Remove(filepath.Join(latest, installedMarker))
	if err := sp.rename(latest, src); err != nil {
		log.Printf("恢复上次没更新完的运行环境：%s 改回 src 失败 %v", latest, err)
		return
	}
	log.Printf("恢复了上次没更新完的运行环境（%s → src），会重跑一遍安装", filepath.Base(latest))
}

// waitServiceGone 等后台服务真的退出：服务退出时会删掉自己的 service.json（hiro.cli.service），最多等 timeout。
func waitServiceGone(home string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		if _, err := os.Stat(filepath.Join(home, "service.json")); errors.Is(err, os.ErrNotExist) {
			time.Sleep(500 * time.Millisecond) // 删完文件到进程真正退出还有一小段
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(300 * time.Millisecond)
	}
}

var errNotBehind = errors.New("程序已经是和界面同一个版本")

// Upgrade 把运行环境换成和安装包同版本的程序（设置页「更新运行环境」）。期间 Hiro 的调用一律回「正在更新运行环境」，
// 不会把后台服务拉起来、也开不了局；关窗口会取消并等回滚做完（StopUpgrade）。
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
	ctx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	b.upMu.Lock()
	b.upCancel, b.upDone = cancel, done
	b.upMu.Unlock()
	defer func() {
		cancel()
		b.upMu.Lock()
		b.upCancel, b.upDone = nil, nil
		b.upMu.Unlock()
		close(done)
	}()
	if b.Hiro != nil {
		b.Hiro.upgrading.Store(true)
		defer b.Hiro.upgrading.Store(false)
	}
	say := func(line string) { send(Progress{Step: "upgrade", Line: line}) }
	say("停下后台服务…")
	shutdownService(5 * time.Second)
	if !waitServiceGone(b.Home, 15*time.Second) {
		return errors.New("后台服务停不下来（可能有任务在跑）：等任务结束后再点一次")
	}
	repo := e.RepoURL
	if repo == "" {
		repo = defaultRepoURL
	}
	plan := e
	plan.FreeBytes = nil // 升级时 .venv 和数据都是搬过去的，用不着首次安装的 3 GB（#204 评审）
	err := swapSource(ctx, swapSpec{
		Home: b.Home,
		Fetch: func(ctx context.Context, dest string, say func(string)) error {
			// 只下这个版本的标签，找不到就报错，不退回 main：换成另一份不知道是什么的代码不如不换
			err := FetchSource(ctx, SourceSpec{Repo: repo, Tag: want, TagOnly: true, Dest: dest, Client: e.HTTP, Backoff: e.Backoff}, say)
			if errors.Is(err, errSourceNotFound) {
				return fmt.Errorf("网上还没有 %s 这个版本的程序（测试包）：不用更新，等正式发布后再点", strings.TrimPrefix(want, "desktop-v"))
			}
			return err
		},
		Install: func(ctx context.Context, say func(string)) error {
			steps, perr := PlanBootstrap(plan)
			if perr != nil {
				return errors.New(perr.Message + "。" + perr.Hint)
			}
			return runSteps(ctx, steps, send)
		},
	}, say)
	if err != nil {
		return err
	}
	if b.Hiro != nil {
		b.Hiro.reset()
	}
	return nil
}

// StopUpgrade 取消正在进行的「更新运行环境」并等回滚做完（关窗口时调用），最多等 timeout；没在更新时立刻返回。
func (b *Bootstrap) StopUpgrade(timeout time.Duration) {
	b.upMu.Lock()
	cancel, done := b.upCancel, b.upDone
	b.upMu.Unlock()
	if cancel == nil || done == nil {
		return
	}
	cancel()
	select {
	case <-done:
	case <-time.After(timeout):
	}
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
