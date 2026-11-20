package run

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/service"
)

func TestMain(m *testing.M) {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case service.ServiceCommand:
			os.Exit(service.Serve())
		case service.SelftestCommand:
			os.Exit(service.Selftest(os.Args[2:]))
		}
	}
	os.Exit(m.Run())
}

func reg(t *testing.T) (*cli.Registry, string) {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	for _, k := range service.LLMVars {
		t.Setenv(k, "")
	}
	r := cli.New()
	Register(r)
	return r, h
}

func run(t *testing.T, r *cli.Registry, argv ...string) (int, *jsonx.Object, string) {
	t.Helper()
	var out bytes.Buffer
	code := r.Main(argv, &cli.Ctx{Stdout: &out})
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	env, err := jsonx.DecodeObject([]byte(lines[len(lines)-1]))
	if err != nil {
		t.Fatalf("not json %q", out.String())
	}
	return code, env, out.String()
}

func errCode(env *jsonx.Object) string { return jsonx.Str(jsonx.Obj(env, "error"), "code") }

func TestStartAcceptsModeParamsAndChecksThem(t *testing.T) {
	r, _ := reg(t)
	code, env, _ := run(t, r, "run", "start", "hajime", "--idol", "i_card-skin-shro-3-007", "--difficulty", "master", "--support-set", "2")
	p := jsonx.Obj(jsonx.Obj(env, "data"), "pending")
	if code != 3 || !strings.Contains(strings.ToUpper(jsonx.Str(p, "summary")), "MASTER") {
		t.Fatalf("%d %v", code, env)
	}
	for _, bad := range [][]string{
		{"run", "start", "daily", "--items", "presents,nonsense"},
		{"run", "start", "hajime"},
		{"run", "start", "nia"},
		{"run", "start", "nia", "--idol", "x", "--difficulty", "regular"},
		{"run", "start", "no-such-kind"},
	} {
		code, env, _ := run(t, r, bad...)
		if code != 2 || errCode(env) != "bad_args" {
			t.Fatalf("%v: %d %v", bad, code, env)
		}
	}
}

func TestWriteFormationNeedsAPlan(t *testing.T) {
	r, h := reg(t)
	if _, env, _ := run(t, r, "run", "start", "write-formation"); errCode(env) != "no_plan" {
		t.Fatalf("%v", env)
	}
	if err := os.WriteFile(filepath.Join(h, "formation-plan.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	code, env, _ := run(t, r, "run", "start", "write-formation", "--scenario", "nia", "--dry-run")
	if code != 3 || !strings.Contains(jsonx.Str(jsonx.Obj(jsonx.Obj(env, "data"), "pending"), "summary"), "不改") {
		t.Fatalf("%d %v", code, env)
	}
}

func TestFitWindowOnlyOnWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows 上可以调")
	}
	r, _ := reg(t)
	if _, env, _ := run(t, r, "run", "start", "fit-window"); errCode(env) != "windows_only" {
		t.Fatalf("%v", env)
	}
}

func TestParamsKeepOnlyGivenValuesInOrder(t *testing.T) {
	r, _ := reg(t)
	_, env, _ := run(t, r, "run", "start", "hif", "--support-set", "4", "--idol", "x", "--no-llm")
	id := jsonx.Str(jsonx.Obj(jsonx.Obj(env, "data"), "pending"), "id")
	data, err := os.ReadFile(filepath.Join(os.Getenv("HIRO_HOME"), "pending", id+".json"))
	if err != nil {
		t.Fatal(err)
	}
	rec, _ := jsonx.DecodeObject(data)
	a := jsonx.Obj(rec, "args")
	if got := params(cli.Args{Object: a}); strings.Join(got.Keys(), ",") != "idol,no_llm,support_set" {
		t.Fatalf("params %v", got.Keys())
	}
}

func TestFollowIsNotOfferedToAgents(t *testing.T) {
	r, _ := reg(t)
	_, env, _ := run(t, r, "tools")
	for _, v := range jsonx.List(jsonx.Obj(env, "data"), "tools") {
		tool := v.(*jsonx.Object)
		if jsonx.Str(tool, "name") != "run events" {
			continue
		}
		for _, av := range jsonx.List(tool, "args") {
			a := av.(*jsonx.Object)
			if jsonx.Str(a, "name") == "--follow" {
				if ag, _ := a.Get("agent"); ag != false {
					t.Fatal("--follow offered to agents")
				}
				return
			}
		}
	}
	t.Fatal("run events --follow not found")
}

func TestSelftestThroughTheCLI(t *testing.T) {
	r, _ := reg(t)
	defer service.Shutdown()
	code, env, _ := run(t, r, "run", "start", "selftest", "--seconds", "1")
	if code != 3 {
		t.Fatalf("%d %v", code, env)
	}
	code, env, _ = run(t, r, "confirm", jsonx.Str(jsonx.Obj(jsonx.Obj(env, "data"), "pending"), "id"))
	job := jsonx.Obj(jsonx.Obj(env, "data"), "job")
	if code != 0 || jsonx.Str(job, "kind") != "selftest" {
		t.Fatalf("%d %v", code, env)
	}
	// --follow：每条事件一行 {"event": …}，最后一行是信封
	code, env, raw := run(t, r, "run", "events", "--job", jsonx.Str(job, "id"), "--follow")
	lines := strings.Split(strings.TrimSpace(raw), "\n")
	if code != 0 || len(lines) < 3 || !strings.HasPrefix(lines[0], `{"event":`) || jsonx.Str(jsonx.Obj(env, "data"), "state") != "done" {
		t.Fatalf("%d %s", code, raw)
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		_, env, _ = run(t, r, "run", "status")
		if jsonx.IsNull(jsonx.Obj(env, "data"), "current") {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !jsonx.IsNull(jsonx.Obj(env, "data"), "current") {
		t.Fatalf("status %v", env)
	}
	if _, env, _ := run(t, r, "run", "pause"); errCode(env) != "no_job" {
		t.Fatalf("pause after done %v", env)
	}
	if _, env, _ := run(t, r, "run", "report", "--job", "nope"); errCode(env) != "no_trace" {
		t.Fatalf("report %v", env)
	}
}
