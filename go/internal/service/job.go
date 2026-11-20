package service

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

// Live 是还在跑的状态。
var Live = []string{"running", "paused", "takeover"}

// LLMVars 是对局里用的 LLM 设置（hiro.brain.llm.from_spec 读这些）：桌面程序按模型设置在确认开局时传过来；HIRO_LLM=off 只用规则和算法。
var LLMVars = []string{"HIRO_LLM", "OPENAI_BASE_URL", "OPENAI_API_KEY", "HIRO_CODEX_BIN"}

const (
	stopGrace = 10 * time.Second
	tracePoll = 500 * time.Millisecond
)

func isLive(state string) bool {
	for _, s := range Live {
		if s == state {
			return true
		}
	}
	return false
}

func epoch(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// Job 是一个任务：一个子进程（argv 是列表，不走 shell），stdout / stderr 的每一行变成一条事件，结束时补一条 done / failed / stopped。
// 事件写 HIRO_HOME/jobs/<编号>/events.jsonl，每行 {"seq","t","type","text",…}。
// 暂停 / 接手：写这个任务的 HIRO_PAUSE_FLAG 旗标文件（主循环在检查点停下，不再点击）；继续：删掉旗标。
type Job struct {
	ID     string
	Kind   *Kind
	Params *jsonx.Object
	Dir    string // HIRO_HOME/jobs/<编号>
	Work   string // HIRO_HOME/work（子进程的当前目录）
	Flag   string // 暂停旗标
	Trace  string // HIRO_HOME/work/traces/<编号>

	rt      Runtime
	home    string
	llmEnv  map[string]string // 只进子进程的环境，不写 job.json、不进事件（OPENAI_API_KEY）
	llmOff  bool
	onExit  func(*Job)
	started float64

	mu       sync.Mutex // 守着下面这些
	state    string
	ended    *float64
	exitCode *int
	pid      int
	seq      int
	cmd      *exec.Cmd

	tailMu  sync.Mutex
	tailPos int64
	done    chan struct{}
}

func newID() string {
	b := make([]byte, 2)
	_, _ = rand.Read(b)
	return time.Now().Format("20060102-150405-") + hex.EncodeToString(b)
}

// NewJob 建一个任务（还没启动）：建任务目录、工作目录。
func NewJob(home string, kind *Kind, params *jsonx.Object, rt Runtime, llmEnv map[string]string, onExit func(*Job)) (*Job, error) {
	if params == nil {
		params = jsonx.NewObject()
	}
	llm := map[string]string{}
	for _, k := range LLMVars {
		if v := llmEnv[k]; v != "" {
			llm[k] = v
		}
	}
	off := strings.EqualFold(llm["HIRO_LLM"], "off")
	if off {
		delete(llm, "HIRO_LLM")
		cp := jsonx.NewObject()
		for _, k := range params.Keys() {
			v, _ := params.Get(k)
			cp.Set(k, v)
		}
		params = cp.Set("no_llm", true)
	}
	id := newID()
	dir := filepath.Join(home, "jobs", id)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return nil, err
	}
	work := filepath.Join(home, "work")
	if err := os.MkdirAll(work, 0o750); err != nil {
		return nil, err
	}
	return &Job{
		ID: id, Kind: kind, Params: params, Dir: dir, Work: work,
		Flag: filepath.Join(dir, "pause.flag"), Trace: filepath.Join(work, "traces", id),
		rt: rt, home: home, llmEnv: llm, llmOff: off, onExit: onExit, started: epoch(time.Now()),
		state: "running", done: make(chan struct{}),
	}, nil
}

// State 现在的状态。
func (j *Job) State() string { j.mu.Lock(); defer j.mu.Unlock(); return j.state }

func (j *Job) setState(s string) { j.mu.Lock(); j.state = s; j.mu.Unlock() }

// Done 任务结束（结束事件写好、状态改好）时关闭。
func (j *Job) Done() <-chan struct{} { return j.done }

// Emit 记一条事件。extra 的键值对排在 text 后面。
func (j *Job) Emit(typ, text string, extra ...any) *jsonx.Object {
	j.mu.Lock()
	defer j.mu.Unlock()
	ev := jsonx.NewObject().Set("seq", j.seq).Set("t", epoch(time.Now())).Set("type", typ).Set("text", text)
	for i := 0; i+1 < len(extra); i += 2 {
		k, _ := extra[i].(string)
		ev.Set(k, extra[i+1])
	}
	j.seq++
	line, err := jsonx.Marshal(ev)
	if err == nil {
		if f, err := os.OpenFile(filepath.Join(j.Dir, "events.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600); err == nil {
			_, _ = f.Write(append(line, '\n'))
			_ = f.Close()
		}
	}
	return ev
}

// Events 读这个任务 seq >= since 的事件。
func (j *Job) Events(since int64) []any {
	return readEvents(filepath.Join(j.Dir, "events.jsonl"), since)
}

func readEvents(path string, since int64) []any {
	out := []any{}
	data, err := os.ReadFile(path)
	if err != nil {
		return out
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		ev, err := jsonx.DecodeObject([]byte(line))
		if err != nil {
			continue
		}
		if n, _ := jsonx.Int(ev, "seq"); n >= since {
			out = append(out, ev)
		}
	}
	return out
}

// Describe 是 job.json 的内容和界面看到的任务信息（字段和顺序同 Python Job.describe）。
func (j *Job) Describe() *jsonx.Object {
	j.mu.Lock()
	defer j.mu.Unlock()
	var ended, code, pid any
	if j.ended != nil {
		ended = *j.ended
	}
	if j.exitCode != nil {
		code = *j.exitCode
	}
	if j.pid != 0 {
		pid = j.pid
	}
	return jsonx.NewObject().Set("id", j.ID).Set("kind", j.Kind.Name).Set("params", j.Params).Set("state", j.state).
		Set("summary", j.Kind.Summary(j.Params)).Set("started_at", j.started).Set("ended_at", ended).Set("exit_code", code).
		Set("pause_flag", j.Flag).Set("dir", j.Dir).Set("pid", pid).Set("pausable", j.Kind.Pausable)
}

func (j *Job) save() {
	if data, err := jsonx.Marshal(j.Describe()); err == nil {
		_ = os.WriteFile(filepath.Join(j.Dir, "job.json"), data, 0o600)
	}
}

// ChildEnv 是子进程的环境：主数据 / 自建素材 / 编成方案的路径（没设才补）、任务种类的变量、编成号、模型设置、暂停旗标、PYTHONPATH。
func (j *Job) ChildEnv() map[string]string {
	env := map[string]string{}
	for _, kv := range os.Environ() {
		if k, v, ok := strings.Cut(kv, "="); ok {
			env[k] = v
		}
	}
	defaults := map[string]string{"HIRO_FORMATION_PLAN": filepath.Join(j.home, "formation-plan.json")}
	if j.rt.Repo != "" {
		defaults["HIRO_KAA_GAME_DATA"] = filepath.Join(j.rt.Repo, "resources", "game_data")
		defaults["HIRO_USER_ASSETS"] = filepath.Join(j.rt.Repo, "resources", "user_assets")
	}
	for k, v := range defaults {
		if env[k] == "" {
			env[k] = v
		}
	}
	for k, v := range j.Kind.Env {
		env[k] = v
	}
	if j.Kind.Name == "hif" { // run_hif 的编成号走环境变量
		for _, kv := range [][2]string{{"support_set", "HIRO_SUPPORT_SET"}, {"memory_set", "HIRO_MEMORY_SET"}} {
			if s := numStr(j.Params, kv[0]); s != "" {
				env[kv[1]] = s
			}
		}
	}
	for k, v := range j.llmEnv {
		env[k] = v
	}
	if j.llmOff {
		delete(env, "HIRO_LLM")
	}
	env["HIRO_PAUSE_FLAG"] = j.Flag
	env["PYTHONUNBUFFERED"] = "1"
	env["PYTHONUTF8"] = "1"
	if j.rt.Repo != "" {
		src := filepath.Join(j.rt.Repo, "src")
		if old := env["PYTHONPATH"]; old != "" {
			src += string(os.PathListSeparator) + old
		}
		env["PYTHONPATH"] = src
	}
	return env
}

// Start 起子进程，开始收输出和跟读 trace。
func (j *Job) Start() error {
	envMap := j.ChildEnv()
	env := make([]string, 0, len(envMap))
	for k, v := range envMap {
		env = append(env, k+"="+v)
	}
	argv := j.Kind.Argv(j.Params, j.Dir, j.rt)
	j.Emit("start", j.Kind.Summary(j.Params), "kind", j.Kind.Name)
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = j.Work
	cmd.Env = env
	proc.Hide(cmd)
	r, w, err := os.Pipe()
	if err != nil {
		return err
	}
	cmd.Stdout, cmd.Stderr = w, w
	if err := cmd.Start(); err != nil {
		_ = r.Close()
		_ = w.Close()
		return err
	}
	_ = w.Close() // 子进程拿着写端；它退出时读端收到 EOF
	j.mu.Lock()
	j.cmd, j.pid = cmd, cmd.Process.Pid
	j.mu.Unlock()
	j.save()
	go j.tail()
	go j.pump(r)
	return nil
}

func (j *Job) exited() bool {
	select {
	case <-j.done:
		return true
	default:
		return false
	}
}

func (j *Job) tail() {
	t := time.NewTicker(tracePoll)
	defer t.Stop()
	for {
		select {
		case <-j.done:
			return
		case <-t.C:
			j.readSteps()
		}
	}
}

// readSteps 读 trace 新写的完整行，每一步变成一条 step 事件。
func (j *Job) readSteps() {
	j.tailMu.Lock()
	defer j.tailMu.Unlock()
	f, err := os.Open(filepath.Join(j.Trace, "steps.jsonl"))
	if err != nil {
		return
	}
	defer f.Close()
	if _, err := f.Seek(j.tailPos, io.SeekStart); err != nil {
		return
	}
	br := bufio.NewReaderSize(f, 1<<20)
	for {
		raw, err := br.ReadBytes('\n')
		if err != nil {
			return // 半行：下次再读
		}
		j.tailPos += int64(len(raw))
		rec, err := jsonx.DecodeObject(raw)
		if err != nil {
			continue
		}
		ev := stepEvent(rec, j.Trace)
		text := jsonx.Str(ev, "text")
		ev.Delete("text")
		extra := make([]any, 0, ev.Len()*2)
		for _, k := range ev.Keys() {
			v, _ := ev.Get(k)
			extra = append(extra, k, v)
		}
		j.Emit("step", text, extra...)
	}
}

// stepEvent：trace 的一步 → 事件字段：页面、动作、理由、谁定的（规则 / 算法 / LLM / 兜底）、截图。
func stepEvent(rec *jsonx.Object, trace string) *jsonx.Object {
	obs, dec := jsonx.Obj(rec, "observation"), jsonx.Obj(rec, "decision")
	seq, _ := rec.Get("seq")
	var frame any
	n, ok := jsonx.AsInt(seq)
	if !ok {
		if s, isStr := seq.(string); isStr {
			if v, err := strconv.ParseInt(s, 10, 64); err == nil {
				n, ok = v, true
			}
		}
	}
	if ok {
		p := filepath.Join(trace, "frames", fmt.Sprintf("%05d.jpg", n))
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			frame = p
		}
	}
	action := jsonx.Obj(dec, "action")
	get := func(o *jsonx.Object, k string) any { v, _ := o.Get(k); return v }
	orList := func(o *jsonx.Object, k string) any {
		if l := jsonx.List(o, k); len(l) > 0 {
			return l
		}
		return []any{}
	}
	text := jsonx.Str(dec, "reason")
	return jsonx.NewObject().Set("step", seq).Set("page", get(jsonx.Obj(obs, "page"), "name")).Set("action", get(action, "kind")).
		Set("args", get(action, "args")).Set("source", get(dec, "source")).Set("decider", get(dec, "decider")).
		Set("warnings", orList(dec, "warnings")).Set("notes", orList(rec, "notes")).Set("frame", frame).Set("text", text)
}

func (j *Job) pump(r *os.File) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64<<10), 4<<20)
	for sc.Scan() {
		line := strings.ToValidUTF8(strings.TrimRight(sc.Text(), "\r"), "�")
		if line != "" {
			j.Emit("log", line)
		}
	}
	_ = r.Close()
	j.mu.Lock()
	cmd := j.cmd
	j.mu.Unlock()
	err := cmd.Wait()
	code := 0
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code = ee.ExitCode()
	} else if err != nil {
		code = -1
	}
	j.readSteps() // 最后几步
	_ = os.Remove(j.Flag)
	ended := epoch(time.Now())
	j.mu.Lock()
	j.exitCode, j.ended = &code, &ended
	j.mu.Unlock()
	if j.onExit != nil { // 扫描结果入库等收尾：在结束事件之前做完，客户端看到结束时信息库已经更新
		func() {
			defer func() {
				if p := recover(); p != nil {
					j.Emit("import_failed", fmt.Sprintf("收尾出错：%v", p))
				}
			}()
			j.onExit(j)
		}()
	}
	// 先写结束事件、再改状态：客户端读到结束状态时，结束事件（含失败原因）一定已经在事件流里
	switch {
	case j.State() == "stopping":
		j.Emit("stopped", "已停止")
		j.setState("stopped")
	case code == 0:
		j.Emit("done", "完成")
		j.setState("done")
	default:
		j.Emit("failed", fmt.Sprintf("任务出错退出（代码 %d），看上面的日志", code), "exit_code", code)
		j.setState("failed")
	}
	j.save()
	close(j.done)
}

// Pause 写暂停旗标（state 是 paused 或 takeover）。
func (j *Job) Pause(why, state string) {
	_ = os.WriteFile(j.Flag, []byte(fmt.Sprintf("%d %s\n", time.Now().Unix(), why)), 0o600)
	j.setState(state)
	j.Emit(state, why)
	j.save()
}

// Resume 删掉暂停旗标。
func (j *Job) Resume() {
	_ = os.Remove(j.Flag)
	j.setState("running")
	j.Emit("running", "继续自动打")
	j.save()
}

// Stop 停止子进程（不放弃培育：游戏停在原地）。先礼后兵：结束信号，10 秒还没退就强杀。
func (j *Job) Stop() {
	j.mu.Lock()
	cmd := j.cmd
	j.mu.Unlock()
	if cmd == nil || j.exited() {
		return
	}
	j.setState("stopping")
	_ = os.Remove(j.Flag)
	proc.Terminate(cmd.Process)
	select {
	case <-j.done:
	case <-time.After(stopGrace):
		_ = cmd.Process.Kill()
	}
}
