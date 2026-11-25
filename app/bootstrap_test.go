package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func bootEnv(goos string, have ...string) BootEnv {
	set := map[string]bool{}
	for _, h := range have {
		set[h] = true
	}
	return BootEnv{
		Home: "/h", Resources: "/r", GOOS: goos, RepoURL: "https://github.com/matto49/hiro-gakumas-hero", Ref: "main",
		LookPath: func(name string) (string, error) {
			if set["path:"+name] {
				return "/usr/bin/" + name, nil
			}
			return "", errors.New("not found")
		},
		Exists: func(p string) bool { return set[filepath.ToSlash(p)] },
	}
}

func ids(steps []Step) string {
	var s []string
	for _, st := range steps {
		s = append(s, st.ID)
	}
	return strings.Join(s, ",")
}

func TestPlanFreshMacDownloadsSourceThenRunsSetupWithBundledUV(t *testing.T) {
	// 没有 git 也能装：PATH 上什么都没有，只有程序自带的 uv
	steps, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv"))
	if err != nil {
		t.Fatal(err)
	}
	if ids(steps) != "source,setup" {
		t.Fatalf("steps %s", ids(steps))
	}
	if steps[0].Run == nil || len(steps[0].Argv) != 0 {
		t.Fatalf("下载源码是程序自己做的一步，不调外部命令：%+v", steps[0])
	}
	if steps[1].Argv[0] != "bash" || !strings.HasSuffix(steps[1].Argv[1], "scripts/setup.sh") || steps[1].Dir != "/h/src" {
		t.Fatalf("setup %+v", steps[1])
	}
	if !hasEnvPrefix(steps[1].Env, "PATH=/r/bin") {
		t.Fatalf("uv dir must lead PATH: %v", steps[1].Env)
	}
}

func TestPlanSourceStepDownloadsTheTagOfThisVersion(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/tags/desktop-v0.1.1.tar.gz": makeArchive(t, "hiro-gakumas-hero-desktop-v0.1.1", fakeCommit, repoFiles...),
	})
	home := t.TempDir()
	e := bootEnv("darwin", "/r/bin/uv")
	e.Home, e.RepoURL, e.Ref, e.Version, e.HTTP, e.Backoff = home, gh.URL, "", "v0.1.1", gh.Client(), time.Millisecond
	steps, perr := PlanBootstrap(e)
	if perr != nil {
		t.Fatal(perr)
	}
	if err := steps[0].Run(context.Background(), func(string) {}); err != nil {
		t.Fatal(err)
	}
	if gh.hitList() != "/archive/refs/tags/desktop-v0.1.1.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
	if info := readSourceInfo(t, filepath.Join(home, "src")); info["source"] != "tag" || info["ref"] != "desktop-v0.1.1" {
		t.Fatalf("info %v", info)
	}
}

func TestPlanSourceStepUsesMainWhenThereIsNoVersion(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/heads/main.tar.gz": makeArchive(t, "hiro-gakumas-hero-main", "", repoFiles...),
	})
	home := t.TempDir()
	e := bootEnv("darwin", "/r/bin/uv")
	e.Home, e.RepoURL, e.Ref, e.Version, e.HTTP, e.Backoff = home, gh.URL, "", "", gh.Client(), time.Millisecond
	steps, _ := PlanBootstrap(e)
	if err := steps[0].Run(context.Background(), func(string) {}); err != nil {
		t.Fatal(err)
	}
	if gh.hitList() != "/archive/refs/heads/main.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
}

func TestPlanSourceStepHonoursAnExplicitRefWithoutFallback(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{})
	e := bootEnv("darwin", "/r/bin/uv")
	e.Home, e.RepoURL, e.Ref, e.Version, e.HTTP, e.Backoff = t.TempDir(), gh.URL, "feat_x", "0.1.1", gh.Client(), time.Millisecond
	steps, _ := PlanBootstrap(e)
	if err := steps[0].Run(context.Background(), func(string) {}); err == nil {
		t.Fatal("指定了分支但下不到，应该报错而不是悄悄换成 main")
	}
	if gh.hitList() != "/archive/feat_x.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
}

func TestPlanLeavesSourceAloneForAnOldGitCloneAndForAHalfInstalledSrc(t *testing.T) {
	// 以前 git clone 的安装：没有标记就只补 setup，不重新下载
	steps, _ := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/.git", "/h/src/scripts/setup.sh"))
	if ids(steps) != "setup" {
		t.Fatalf("steps %s", ids(steps))
	}
	// 新装的 src（没有 .git）装到一半：同样只补 setup
	steps, _ = PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/scripts/setup.sh", "/h/src/.venv/bin/python"))
	if ids(steps) != "setup" {
		t.Fatalf("steps %s", ids(steps))
	}
}

func TestPlanIsEmptyOnceInstalled(t *testing.T) {
	steps, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/.git", "/h/src/.venv/bin/python", "/h/src/.hiro-installed"))
	if err != nil || len(steps) != 0 {
		t.Fatalf("steps %v err %v", ids(steps), err)
	}
}

func TestPlanIsEmptyOnceInstalledWithoutGit(t *testing.T) {
	// 新装的 src 没有 .git：看 venv 的 python 和装完标记就够了
	steps, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/scripts/setup.sh", "/h/src/.venv/bin/python", "/h/src/.hiro-installed"))
	if err != nil || len(steps) != 0 {
		t.Fatalf("steps %v err %v", ids(steps), err)
	}
	steps, err = PlanBootstrap(bootEnv("windows", "/r/bin/uv.exe", "/h/src/.venv/Scripts/python.exe", "/h/src/.hiro-installed"))
	if err != nil || len(steps) != 0 {
		t.Fatalf("steps %v err %v", ids(steps), err)
	}
}

func TestRunLinesDoesNotDownloadInDevWhenHiroCLIIsSet(t *testing.T) {
	t.Setenv("HIRO_CLI", `["hiro"]`)
	home := filepath.Join(t.TempDir(), "home")
	b := &Bootstrap{Home: home, Hiro: &Hiro{}}
	var lines []string
	if err := b.RunLines(context.Background(), func(p Progress) { lines = append(lines, p.Line) }); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, "src")); err == nil {
		t.Fatal("开发环境不该下载源码")
	}
}

func TestPlanUsesChinaMirrorsWhenAsked(t *testing.T) {
	e := bootEnv("darwin", "/r/bin/uv", "/h/src/.git")
	e.MirrorCN = true
	steps, _ := PlanBootstrap(e)
	if ids(steps) != "setup" || !hasEnvPrefix(steps[0].Env, "UV_INDEX_URL=") || !hasEnvPrefix(steps[0].Env, "UV_PYTHON_INSTALL_MIRROR=") {
		t.Fatalf("steps %v env %v", ids(steps), steps[0].Env)
	}
}

func TestPlanExplainsMissingUV(t *testing.T) {
	_, err := PlanBootstrap(bootEnv("windows"))
	if err == nil || err.Code != "uv_missing" {
		t.Fatalf("err %+v", err)
	}
}

func TestPlanWindowsRunsPowerShellSetup(t *testing.T) {
	steps, _ := PlanBootstrap(bootEnv("windows", "/r/bin/uv.exe", "/h/src/.git"))
	if ids(steps) != "setup,simd" || steps[0].Argv[0] != "powershell" || !strings.Contains(strings.Join(steps[0].Argv, " "), "setup.ps1") {
		t.Fatalf("setup %+v", steps)
	}
	if steps[0].Optional {
		t.Fatal("安装脚本本身是必做的")
	}
}

func TestPlanWindowsInstallsSimdOcrAsAnOptionalStepAfterSetup(t *testing.T) {
	steps, _ := PlanBootstrap(bootEnv("windows", "/r/bin/uv.exe", "/h/src/.git"))
	if !strings.HasSuffix(steps[0].Argv[len(steps[0].Argv)-1], "-NoSimd") {
		t.Fatalf("setup.ps1 里的 SIMD 项要跳过（单独一步装）：%v", steps[0].Argv)
	}
	simd := steps[1]
	if !simd.Optional || filepath.ToSlash(simd.Dir) != "/h/src" {
		t.Fatalf("SIMD 是可选步骤：%+v", simd)
	}
	if got := filepath.ToSlash(simd.Argv[0]); got != "/h/src/.venv/Scripts/python.exe" || filepath.ToSlash(simd.Argv[1]) != "/h/src/tools/assets/simd_ocr.py" {
		t.Fatalf("用 venv 里的 python 跑安装脚本：%v", simd.Argv)
	}
}

func TestPlanOtherSystemsDoNotInstallSimd(t *testing.T) {
	for _, goos := range []string{"darwin", "linux"} {
		steps, _ := PlanBootstrap(bootEnv(goos, "/r/bin/uv", "/h/src/.git"))
		if ids(steps) != "setup" || strings.Contains(strings.Join(steps[0].Argv, " "), "NoSimd") {
			t.Fatalf("%s: Mac 用 Apple Vision，什么都不装：%s %v", goos, ids(steps), steps[0].Argv)
		}
	}
}

// 假命令：测试程序自己当子进程（helper process 做法），按 HIRO_FAKE_EXIT 退出。
func TestHelperProcess(t *testing.T) {
	if os.Getenv("HIRO_FAKE_STEP") == "" {
		return
	}
	fmt.Println("fake step output")
	if os.Getenv("HIRO_FAKE_EXIT") == "1" {
		os.Exit(1)
	}
	os.Exit(0)
}

func fakeCmd(exit string) ([]string, []string) {
	return []string{os.Args[0], "-test.run=TestHelperProcess"}, []string{"HIRO_FAKE_STEP=1", "HIRO_FAKE_EXIT=" + exit}
}

func collect(steps []Step) (error, string) {
	var lines []string
	err := runSteps(context.Background(), steps, func(p Progress) { lines = append(lines, p.Step+":"+p.Line) })
	return err, strings.Join(lines, "\n")
}

func TestOptionalCommandStepFailureOnlyWarns(t *testing.T) {
	argv, env := fakeCmd("1")
	okArgv, okEnv := fakeCmd("0")
	steps := []Step{
		{ID: "simd", Title: "装 SIMD", Argv: argv, Env: env, Optional: true},
		{ID: "after", Title: "后一步", Argv: okArgv, Env: okEnv},
	}
	err, out := collect(steps)
	if err != nil {
		t.Fatalf("可选步骤失败不该让安装失败：%v", err)
	}
	if !strings.Contains(out, "simd:⚠ 装 SIMD没做成") || !strings.Contains(out, "after:fake step output") {
		t.Fatalf("要提示一句、并继续后面的步骤：\n%s", out)
	}
}

func TestRequiredCommandStepFailureStopsTheInstall(t *testing.T) {
	argv, env := fakeCmd("1")
	okArgv, okEnv := fakeCmd("0")
	err, out := collect([]Step{{ID: "setup", Title: "装环境", Argv: argv, Env: env}, {ID: "after", Title: "后一步", Argv: okArgv, Env: okEnv}})
	if err == nil || !strings.Contains(err.Error(), "装环境失败") || strings.Contains(out, "after:") {
		t.Fatalf("必做步骤失败要停：err=%v\n%s", err, out)
	}
}

func TestOptionalStepThatCannotStartOrErrorsOnlyWarns(t *testing.T) {
	steps := []Step{
		{ID: "a", Title: "找不到程序", Argv: []string{filepath.Join(t.TempDir(), "nope")}, Optional: true},
		{ID: "b", Title: "自己做的", Optional: true, Run: func(context.Context, func(string)) error { return errors.New("网络不通") }},
	}
	if err, out := collect(steps); err != nil || !strings.Contains(out, "b:⚠ 自己做的没做成") {
		t.Fatalf("err=%v\n%s", err, out)
	}
}

func TestCancelledOptionalStepStillStopsTheInstall(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	step := Step{ID: "b", Title: "自己做的", Optional: true, Run: func(context.Context, func(string)) error { cancel(); return ctx.Err() }}
	if err := runSteps(ctx, []Step{step}, func(Progress) {}); err == nil {
		t.Fatal("用户取消安装时不能继续往下装")
	}
}

func hasEnvPrefix(env []string, p string) bool {
	for _, e := range env {
		if strings.HasPrefix(e, p) {
			return true
		}
	}
	return false
}

func TestPlanRefusesWhenTheDiskIsTooFull(t *testing.T) {
	e := bootEnv("darwin", "/r/bin/uv")
	e.FreeBytes = func(string) (uint64, error) { return 2 << 30, nil }
	_, err := PlanBootstrap(e)
	if err == nil || err.Code != "disk_full" || !strings.Contains(err.Message, "2.0 GB") {
		t.Fatalf("err %+v", err)
	}
	e.FreeBytes = func(string) (uint64, error) { return 10 << 30, nil }
	if _, err := PlanBootstrap(e); err != nil {
		t.Fatalf("err %+v", err)
	}
}

func TestFreeBytesReadsTheRealDisk(t *testing.T) {
	n, err := freeBytes(filepath.Join(t.TempDir(), "not", "yet"))
	if err != nil || n == 0 {
		t.Fatalf("free %d err %v", n, err)
	}
}

func TestHalfFinishedInstallIsNotReady(t *testing.T) {
	// setup.sh 第 1 步就建好了 .venv：没有「装完」标记就还要再跑 setup
	steps, _ := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/.git", "/h/src/.venv/bin/python"))
	if ids(steps) != "setup" {
		t.Fatalf("steps %s", ids(steps))
	}
	steps, _ = PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "/h/src/.git", "/h/src/.venv/bin/python", "/h/src/.hiro-installed"))
	if len(steps) != 0 {
		t.Fatalf("steps %s", ids(steps))
	}
}

func TestResetRemovesTheMarker(t *testing.T) {
	home := t.TempDir()
	_ = os.MkdirAll(filepath.Join(home, "src"), 0o755)
	m := filepath.Join(home, "src", installedMarker)
	_ = os.WriteFile(m, nil, 0o644)
	b := &Bootstrap{Home: home, Hiro: &Hiro{}}
	if err := b.Reset(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(m); err == nil {
		t.Fatal("marker still there")
	}
}
