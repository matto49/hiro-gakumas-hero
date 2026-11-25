package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
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

func TestPlanFreshMacClonesThenRunsSetupWithBundledUV(t *testing.T) {
	steps, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "path:git"))
	if err != nil {
		t.Fatal(err)
	}
	if ids(steps) != "source,setup" {
		t.Fatalf("steps %s", ids(steps))
	}
	if strings.Join(steps[0].Argv, " ") != "/usr/bin/git clone --depth 1 --branch main https://github.com/matto49/hiro-gakumas-hero /h/src" {
		t.Fatalf("clone %v", steps[0].Argv)
	}
	if steps[1].Argv[0] != "bash" || !strings.HasSuffix(steps[1].Argv[1], "scripts/setup.sh") || steps[1].Dir != "/h/src" {
		t.Fatalf("setup %+v", steps[1])
	}
	if !hasEnvPrefix(steps[1].Env, "PATH=/r/bin") {
		t.Fatalf("uv dir must lead PATH: %v", steps[1].Env)
	}
}

func TestPlanIsEmptyOnceInstalled(t *testing.T) {
	steps, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "path:git", "/h/src/.git", "/h/src/.venv/bin/python", "/h/src/.hiro-installed"))
	if err != nil || len(steps) != 0 {
		t.Fatalf("steps %v err %v", ids(steps), err)
	}
}

func TestPlanUsesChinaMirrorsWhenAsked(t *testing.T) {
	e := bootEnv("darwin", "/r/bin/uv", "path:git", "/h/src/.git")
	e.MirrorCN = true
	steps, _ := PlanBootstrap(e)
	if ids(steps) != "setup" || !hasEnvPrefix(steps[0].Env, "UV_INDEX_URL=") || !hasEnvPrefix(steps[0].Env, "UV_PYTHON_INSTALL_MIRROR=") {
		t.Fatalf("steps %v env %v", ids(steps), steps[0].Env)
	}
}

func TestPlanExplainsMissingGitAndUV(t *testing.T) {
	_, err := PlanBootstrap(bootEnv("darwin", "/r/bin/uv"))
	if err == nil || err.Code != "git_missing" || !strings.Contains(err.Hint, "xcode-select") {
		t.Fatalf("err %+v", err)
	}
	_, err = PlanBootstrap(bootEnv("windows", "path:git"))
	if err == nil || err.Code != "uv_missing" {
		t.Fatalf("err %+v", err)
	}
}

func TestPlanWindowsRunsPowerShellSetup(t *testing.T) {
	steps, _ := PlanBootstrap(bootEnv("windows", "/r/bin/uv.exe", "path:git", "/h/src/.git"))
	if ids(steps) != "setup" || steps[0].Argv[0] != "powershell" || !strings.Contains(strings.Join(steps[0].Argv, " "), "setup.ps1") {
		t.Fatalf("setup %+v", steps)
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
	e := bootEnv("darwin", "/r/bin/uv", "path:git")
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
	steps, _ := PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "path:git", "/h/src/.git", "/h/src/.venv/bin/python"))
	if ids(steps) != "setup" {
		t.Fatalf("steps %s", ids(steps))
	}
	steps, _ = PlanBootstrap(bootEnv("darwin", "/r/bin/uv", "path:git", "/h/src/.git", "/h/src/.venv/bin/python", "/h/src/.hiro-installed"))
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
