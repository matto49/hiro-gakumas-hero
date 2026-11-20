package pybridge

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// fakeRepo 建一个假仓库：src/hiro/__init__.py，和一个假 python（shell 脚本，把收到的参数和 PYTHONPATH 写回 JSON）。
func fakeRepo(t *testing.T, script string) (repo, py string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("假 python 是 shell 脚本")
	}
	repo = t.TempDir()
	if err := os.MkdirAll(filepath.Join(repo, "src", "hiro"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "src", "hiro", "__init__.py"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	py = filepath.Join(t.TempDir(), "python")
	if err := os.WriteFile(py, []byte("#!/bin/sh\n"+script), 0o700); err != nil { //nolint:gosec // 测试用的假 python，要能执行
		t.Fatal(err)
	}
	return repo, py
}

func TestRunPassesArgvAndPythonPath(t *testing.T) {
	repo, py := fakeRepo(t, `echo "{\"ok\": true, \"data\": {\"argv\": \"$*\", \"pp\": \"$PYTHONPATH\", \"home\": \"$HIRO_HOME\"}}"`+"\n")
	t.Setenv("HIRO_REPO", repo)
	t.Setenv("HIRO_PYTHON", py)
	t.Setenv("HIRO_HOME", "/h")
	t.Setenv("PYTHONPATH", "/extra")
	data, err := CLI("plan", "formation", "--idol", "x")
	if err != nil {
		t.Fatal(err)
	}
	o := data.(*jsonx.Object)
	if jsonx.Str(o, "argv") != "-m hiro.cli.main plan formation --idol x" {
		t.Fatalf("argv %q", jsonx.Str(o, "argv"))
	}
	if jsonx.Str(o, "pp") != filepath.Join(repo, "src")+string(os.PathListSeparator)+"/extra" || jsonx.Str(o, "home") != "/h" {
		t.Fatalf("env %v", o)
	}
}

func TestRunTurnsFailureIntoCliError(t *testing.T) {
	repo, py := fakeRepo(t, `echo "warning on stdout"; echo '{"ok": false, "error": {"code": "no_inventory", "message": "没库存", "hint": "先扫描"}}'; exit 1`+"\n")
	t.Setenv("HIRO_REPO", repo)
	t.Setenv("HIRO_PYTHON", py)
	_, err := CLI("plan", "formation")
	e := clierr.As(err)
	if e.Code != "no_inventory" || e.Hint != "先扫描" || e.Exit != 1 {
		t.Fatalf("%+v", e)
	}
}

func TestRunWithoutJSONExplains(t *testing.T) {
	repo, py := fakeRepo(t, "echo Traceback >&2; exit 1\n")
	t.Setenv("HIRO_REPO", repo)
	t.Setenv("HIRO_PYTHON", py)
	_, err := CLI("x")
	if e := clierr.As(err); e.Code != "python_failed" || !strings.Contains(e.Message, "Traceback") {
		t.Fatalf("%+v", e)
	}
}

func TestPythonMissing(t *testing.T) {
	t.Setenv("HIRO_REPO", t.TempDir())
	t.Setenv("HIRO_PYTHON", filepath.Join(t.TempDir(), "nope"))
	t.Setenv("HIRO_HOME", t.TempDir())
	if _, err := Python(); clierr.As(err).Code != "python_missing" {
		t.Fatalf("%v", err)
	}
}

func TestRepoFoundByWalkingUpFromWorkingDir(t *testing.T) {
	t.Setenv("HIRO_REPO", "")
	// 这个测试本身就在仓库里跑：go/internal/pybridge → 往上三级有 src/hiro/__init__.py
	r, err := Repo()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(r, "src", "hiro", "__init__.py")); err != nil {
		t.Fatalf("repo %q", r)
	}
}

func TestPythonPrefersRepoVenv(t *testing.T) {
	repo, _ := fakeRepo(t, "")
	venv := filepath.Join(repo, ".venv", "bin")
	if err := os.MkdirAll(venv, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(venv, "python"), []byte("#!/bin/sh\n"), 0o700); err != nil { //nolint:gosec // 假 python
		t.Fatal(err)
	}
	t.Setenv("HIRO_REPO", repo)
	t.Setenv("HIRO_PYTHON", "")
	if p, err := Python(); err != nil || p != filepath.Join(venv, "python") {
		t.Fatalf("%q %v", p, err)
	}
}
