package service

import (
	"bytes"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// TestMain：测试程序被重新调起时扮演 hiro 的隐藏子命令（拉起服务、自检任务、测试用的小任务）。
func TestMain(m *testing.M) {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case ServiceCommand:
			os.Exit(Serve())
		case SelftestCommand:
			os.Exit(Selftest(os.Args[2:]))
		case "__copy": // 扫描任务的替身：把 out.json 放好
			data, err := os.ReadFile(os.Args[2])
			if err != nil {
				os.Exit(1)
			}
			_ = os.WriteFile(os.Args[3], data, 0o600)
			os.Exit(0)
		case "__exit0": // 什么都不产出就退出
			os.Exit(0)
		case "__sleep":
			time.Sleep(30 * time.Second)
			os.Exit(0)
		}
	}
	os.Exit(m.Run())
}

func withService(t *testing.T) string {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	for _, k := range append([]string{"HIRO_PAUSE_FLAG"}, LLMVars...) {
		t.Setenv(k, "")
	}
	t.Cleanup(Shutdown)
	return h
}

func data(t *testing.T, path string, body any) *jsonx.Object {
	t.Helper()
	d, err := Data(path, body)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	o, _ := d.(*jsonx.Object)
	return o
}

func start(t *testing.T, kind string, params *jsonx.Object, llm map[string]string) string {
	t.Helper()
	le := jsonx.NewObject()
	for k, v := range llm {
		le.Set(k, v)
	}
	d := data(t, "start", jsonx.NewObject().Set("kind", kind).Set("params", params).Set("llm_env", le))
	return jsonx.Str(jsonx.Obj(d, "job"), "id")
}

func waitState(t *testing.T, want ...string) *jsonx.Object {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	var st *jsonx.Object
	for time.Now().Before(deadline) {
		st = data(t, "status", nil)
		cur := jsonx.Obj(st, "current")
		if cur == nil {
			if r := jsonx.List(st, "recent"); len(r) > 0 {
				cur, _ = r[0].(*jsonx.Object)
			}
		}
		for _, w := range want {
			if cur != nil && jsonx.Str(cur, "state") == w {
				return cur
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("等不到 %v：%v", want, st)
	return nil
}

func events(t *testing.T, job string, since int) []*jsonx.Object {
	t.Helper()
	d := data(t, "events", jsonx.NewObject().Set("job", job).Set("since", since))
	var out []*jsonx.Object
	for _, e := range jsonx.List(d, "events") {
		out = append(out, e.(*jsonx.Object))
	}
	return out
}

func TestStatusSpawnsTheService(t *testing.T) {
	h := withService(t)
	st := data(t, "status", nil)
	if !jsonx.IsNull(st, "current") {
		t.Fatalf("%v", st)
	}
	info, err := readInfo(filepath.Join(h, "service.json"))
	if err != nil || info.Port == 0 || info.Token == "" || info.PID == os.Getpid() {
		t.Fatalf("%+v %v", info, err)
	}
	if fi, _ := os.Stat(filepath.Join(h, "service.json")); fi.Mode().Perm() != 0o600 {
		t.Fatalf("service.json perm %v", fi.Mode().Perm())
	}
}

func TestSelftestJobStreamsEventsUntilDone(t *testing.T) {
	withService(t)
	job := start(t, "selftest", jsonx.NewObject().Set("seconds", 1), nil)
	waitState(t, "done")
	evs := events(t, job, 0)
	var log1, step bool
	for i, e := range evs {
		if n, _ := jsonx.Int(e, "seq"); n != int64(i) {
			t.Fatalf("seq gap at %d: %v", i, e)
		}
		if jsonx.Str(e, "type") == "log" && strings.Contains(jsonx.Str(e, "text"), "selftest 1") {
			log1 = true
		}
		if jsonx.Str(e, "type") == "step" {
			step = true
			if jsonx.Str(e, "page") != "SELFTEST" || jsonx.Str(e, "action") != "Wait" || jsonx.Str(e, "source") != "rule" ||
				jsonx.Str(e, "text") != "自检：不碰游戏" || !strings.HasSuffix(jsonx.Str(e, "frame"), "00001.jpg") {
				t.Fatalf("step %v", e)
			}
			want := "seq,t,type,text,step,page,action,args,source,decider,warnings,notes,frame"
			if strings.Join(e.Keys(), ",") != want {
				t.Fatalf("step keys %v", e.Keys())
			}
		}
	}
	if !log1 || !step || jsonx.Str(evs[len(evs)-1], "type") != "done" || jsonx.Str(evs[0], "type") != "start" {
		t.Fatalf("events %v", evs)
	}
	if got := events(t, job, len(evs)-1); len(got) != 1 {
		t.Fatalf("since: %d", len(got))
	}
}

func TestSecondJobIsBusy(t *testing.T) {
	withService(t)
	start(t, "selftest", jsonx.NewObject().Set("seconds", 5), nil)
	_, err := Data("start", jsonx.NewObject().Set("kind", "selftest").Set("params", jsonx.NewObject()))
	if clierr.As(err).Code != "busy" {
		t.Fatalf("%v", err)
	}
	data(t, "stop", nil)
	waitState(t, "stopped")
}

func TestPauseTakeoverResumeUseTheFlag(t *testing.T) {
	withService(t)
	start(t, "selftest", jsonx.NewObject().Set("seconds", 5), nil)
	cur := waitState(t, "running")
	flag := jsonx.Str(cur, "pause_flag")
	exists := func() bool { _, err := os.Stat(flag); return err == nil }
	data(t, "pause", nil)
	if waitState(t, "paused"); !exists() {
		t.Fatal("pause flag")
	}
	data(t, "takeover", nil)
	if waitState(t, "takeover"); !exists() {
		t.Fatal("takeover flag")
	}
	data(t, "resume", nil)
	if waitState(t, "running"); exists() {
		t.Fatal("resume removes flag")
	}
	data(t, "stop", nil)
	waitState(t, "stopped")
}

func TestRequestsWithoutTokenAreRefused(t *testing.T) {
	h := withService(t)
	data(t, "status", nil)
	info, _ := readInfo(filepath.Join(h, "service.json"))
	resp, err := http.Post("http://127.0.0.1:"+strconv.Itoa(info.Port)+"/status", "application/json", bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestUnknownKindIsAUsageError(t *testing.T) {
	withService(t)
	_, err := Data("start", jsonx.NewObject().Set("kind", "no-such-kind"))
	if e := clierr.As(err); e.Code != "bad_args" || e.Exit != 2 {
		t.Fatalf("%+v", e)
	}
}

func TestModelEnvReachesTheJobButNotDisk(t *testing.T) {
	h := withService(t)
	const key = "sk-test-not-a-real-key-123"
	job := start(t, "selftest", jsonx.NewObject().Set("seconds", 1),
		map[string]string{"HIRO_LLM": "openai:deepseek-chat", "OPENAI_BASE_URL": "https://api.deepseek.com", "OPENAI_API_KEY": key, "PATH": "/evil"})
	waitState(t, "done")
	found := false
	for _, e := range events(t, job, 0) {
		if jsonx.Str(e, "text") == "llm=openai:deepseek-chat base=https://api.deepseek.com key=set" {
			found = true
		}
	}
	if !found {
		t.Fatal("model env did not reach the job")
	}
	var files []string
	_ = filepath.WalkDir(h, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			files = append(files, p)
		}
		return nil
	})
	for _, p := range files {
		if b, _ := os.ReadFile(p); bytes.Contains(b, []byte(key)) {
			t.Fatalf("key leaked into %s", p)
		}
	}
}

func TestSecondServiceExitsWhileFirstHoldsTheLock(t *testing.T) {
	h := withService(t)
	data(t, "status", nil)
	first, _ := readInfo(filepath.Join(h, "service.json"))
	cmd := exec.Command(os.Args[0], ServiceCommand) //nolint:gosec // 测试程序自己扮演 hiro __service
	cmd.Env = os.Environ()
	if err := cmd.Run(); err == nil {
		t.Fatal("second service should exit non-zero")
	}
	if now, _ := readInfo(filepath.Join(h, "service.json")); now.PID != first.PID {
		t.Fatal("service.json was replaced")
	}
}

func TestConcurrentClientsShareOneService(t *testing.T) {
	withService(t)
	var mu sync.Mutex
	pids := map[int64]bool{}
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			d, err := Data("ping", nil)
			if err != nil {
				t.Error(err)
				return
			}
			n, _ := jsonx.Int(d.(*jsonx.Object), "pid")
			mu.Lock()
			pids[n] = true
			mu.Unlock()
		}()
	}
	wg.Wait()
	if len(pids) != 1 {
		t.Fatalf("pids %v", pids)
	}
}

// ---------------------------------------------------------------- 不起服务、直接用 Manager

func testManager(t *testing.T) (*Manager, string) {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	m := NewManager(h)
	m.Resolve = func(string) (Runtime, error) { return Runtime{Self: os.Args[0], Python: "/nonexistent-python"}, nil }
	return m, h
}

func waitJob(t *testing.T, j *Job) {
	t.Helper()
	select {
	case <-j.Done():
	case <-time.After(20 * time.Second):
		t.Fatal("job did not finish")
	}
}

func TestOrphanChildOfADeadServiceIsStoppedOnRestart(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("没有 python3")
	}
	h := t.TempDir()
	child := exec.Command(py, "-c", "import time; time.sleep(60)")
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	d := filepath.Join(h, "jobs", "20261117-000000-abcd")
	if err := os.MkdirAll(d, 0o750); err != nil {
		t.Fatal(err)
	}
	rec := `{"id":"20261117-000000-abcd","state":"running","pid":` + strconv.Itoa(child.Process.Pid) + `}`
	if err := os.WriteFile(filepath.Join(d, "job.json"), []byte(rec), 0o600); err != nil {
		t.Fatal(err)
	}
	NewManager(h)
	select {
	case werr := <-done:
		_ = werr // 被结束了：退出码不重要
	case <-time.After(10 * time.Second):
		_ = child.Process.Kill()
		t.Fatal("orphan not stopped")
	}
	b, _ := os.ReadFile(filepath.Join(d, "job.json"))
	if o, _ := jsonx.DecodeObject(b); jsonx.Str(o, "state") != "lost" {
		t.Fatalf("%s", b)
	}
}

func TestPauseOnANonPausableJobIsRefused(t *testing.T) {
	m, _ := testManager(t)
	Kinds["__sleep-test"] = &Kind{
		Name: "__sleep-test", Summary: fixed("测试"),
		Argv: func(_ *jsonx.Object, _ string, rt Runtime) []string { return []string{rt.Self, "__sleep"} },
	}
	defer delete(Kinds, "__sleep-test")
	if _, err := m.Start("__sleep-test", nil, nil); err != nil {
		t.Fatal(err)
	}
	j := m.current
	defer func() { j.Stop(); waitJob(t, j) }()
	if _, err := m.Control("takeover", ""); clierr.As(err).Code != "not_pausable" {
		t.Fatalf("%v", err)
	}
	if _, err := os.Stat(j.Flag); err == nil {
		t.Fatal("flag should not exist")
	}
}

func TestTerminalEventIsWrittenBeforeTheStateFlips(t *testing.T) {
	m, _ := testManager(t)
	if _, err := m.Start("selftest", jsonx.NewObject().Set("seconds", 1), nil); err != nil {
		t.Fatal(err)
	}
	j := m.current
	for {
		state := j.State()
		evs := j.Events(0)
		if state != "running" {
			last := evs[len(evs)-1].(*jsonx.Object)
			if jsonx.Str(last, "type") != state {
				t.Fatalf("state %s but last event %v", state, last)
			}
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if j.State() != "done" {
		t.Fatalf("state %s", j.State())
	}
	b, _ := os.ReadFile(filepath.Join(j.Dir, "job.json"))
	rec, _ := jsonx.DecodeObject(b)
	if pid, _ := jsonx.Int(rec, "pid"); pid == 0 || jsonx.Str(rec, "state") != "done" {
		t.Fatalf("job.json %s", b)
	}
	if v, _ := rec.Get("pausable"); v != true {
		t.Fatal("pausable")
	}
}

func scanKind(src string) *Kind {
	return &Kind{
		Name: "__scan-test", Summary: fixed("测试扫描"), Output: "supports",
		Argv: func(_ *jsonx.Object, dir string, rt Runtime) []string {
			if src == "" {
				return []string{rt.Self, "__exit0"}
			}
			return []string{rt.Self, "__copy", src, filepath.Join(dir, "out.json")}
		},
	}
}

func TestScanOutputLandsInTheStoreBeforeDone(t *testing.T) {
	m, _ := testManager(t)
	src, _ := filepath.Abs(filepath.Join("..", "..", "..", "tests", "cli", "data", "support-inventory.json"))
	Kinds["__scan-test"] = scanKind(src)
	defer delete(Kinds, "__scan-test")
	if _, err := m.Start("__scan-test", nil, nil); err != nil {
		t.Fatal(err)
	}
	waitJob(t, m.current)
	var types []string
	for _, e := range m.current.Events(0) {
		o := e.(*jsonx.Object)
		types = append(types, jsonx.Str(o, "type"))
		if jsonx.Str(o, "type") == "imported" && !strings.Contains(jsonx.Str(o, "text"), "4") {
			t.Fatalf("%v", o)
		}
	}
	if strings.Join(types, ",") != "start,imported,done" {
		t.Fatalf("%v", types)
	}
}

func TestMissingScanOutputIsReportedNotImported(t *testing.T) {
	m, _ := testManager(t)
	Kinds["__scan-test"] = scanKind("")
	defer delete(Kinds, "__scan-test")
	if _, err := m.Start("__scan-test", nil, nil); err != nil {
		t.Fatal(err)
	}
	waitJob(t, m.current)
	found := false
	for _, e := range m.current.Events(0) {
		found = found || jsonx.Str(e.(*jsonx.Object), "type") == "import_failed"
	}
	if !found {
		t.Fatal("import_failed missing")
	}
}

func TestChildEnv(t *testing.T) {
	h := t.TempDir()
	t.Setenv("HIRO_KAA_GAME_DATA", "")
	t.Setenv("HIRO_FORMATION_PLAN", "")
	t.Setenv("PYTHONPATH", "/old")
	rt := Runtime{Python: "/py", Repo: "/repo"}
	j, err := NewJob(h, Kinds["hif"], jsonx.NewObject().Set("support_set", 4).Set("memory_set", 5), rt,
		map[string]string{"HIRO_LLM": "codex", "PATH": "/evil", "PYTHONPATH": "/evil"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	env := j.ChildEnv()
	checks := map[string]string{
		"HIRO_KAA_GAME_DATA": filepath.Join("/repo", "resources", "game_data"), "HIRO_USER_ASSETS": filepath.Join("/repo", "resources", "user_assets"),
		"HIRO_FORMATION_PLAN": filepath.Join(h, "formation-plan.json"), "HIRO_SUPPORT_SET": "4", "HIRO_MEMORY_SET": "5",
		"HIRO_LLM": "codex", "HIRO_RUNTIME": "own", "HIRO_PAUSE_FLAG": j.Flag,
		"PYTHONPATH": filepath.Join("/repo", "src") + string(os.PathListSeparator) + "/old",
	}
	for k, v := range checks {
		if env[k] != v {
			t.Fatalf("%s = %q want %q", k, env[k], v)
		}
	}
	if env["PATH"] == "/evil" {
		t.Fatal("only model variables are forwarded")
	}
	t.Setenv("HIRO_KAA_GAME_DATA", "/somewhere/game_data")
	if env := j.ChildEnv(); env["HIRO_KAA_GAME_DATA"] != "/somewhere/game_data" {
		t.Fatal("explicit env wins")
	}
	off, _ := NewJob(h, Kinds["hif"], jsonx.NewObject(), rt, map[string]string{"HIRO_LLM": "off"}, nil)
	if _, ok := off.ChildEnv()["HIRO_LLM"]; ok || !strings.Contains(strings.Join(off.Kind.Argv(off.Params, off.Dir, rt), " "), "--no-llm") {
		t.Fatal("HIRO_LLM=off → --no-llm and no HIRO_LLM")
	}
}
