package service

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/info"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
	"github.com/matto49/hiro-gakumas-hero/go/internal/report"
)

const recentN = 10

// produceKinds 对局：结束时出成绩摘要。
var produceKinds = map[string]bool{"hif": true, "hajime": true, "nia": true, "tower": true}

// Manager 管任务：同一时间只跑一个，记最近的任务。
type Manager struct {
	home    string
	mu      sync.Mutex
	current *Job
	jobs    map[string]*Job
	// Resolve 找起子进程要的 Python / 仓库 / hiro 自己（测试里换）。
	Resolve func(kind string) (Runtime, error)
}

// NewManager 建管理器；上一个服务没结束的任务标成 lost（先停掉它们留下的子进程，免得两个主循环同时操作游戏）。
func NewManager(home string) *Manager {
	m := &Manager{home: home, jobs: map[string]*Job{}, Resolve: resolveRuntime}
	m.markLost()
	return m
}

func resolveRuntime(kind string) (Runtime, error) {
	self, err := os.Executable()
	if err != nil {
		return Runtime{}, err
	}
	rt := Runtime{Self: self}
	if repo, err := pybridge.Repo(); err == nil {
		rt.Repo = repo
	}
	if kind == "selftest" || kind == "fit-window" { // 自检、调窗口是 hiro 自己的子命令，不要 Python
		return rt, nil
	}
	if rt.Repo == "" {
		_, err := pybridge.Repo()
		return rt, err
	}
	py, err := pybridge.Python()
	if err != nil {
		return rt, err
	}
	rt.Python = py
	return rt, nil
}

func jobsDir(home string) string { return filepath.Join(home, "jobs") }

// isOurs：pid 还活着，而且是 Python 或 hiro（防 pid 被别的程序复用时误杀）。
func isOurs(pid int) bool {
	cl := strings.ToLower(proc.CommandLine(pid))
	return strings.Contains(cl, "python") || strings.Contains(cl, "hiro")
}

func killOrphan(pid int) {
	if pid <= 0 || pid == os.Getpid() || !isOurs(pid) {
		return
	}
	p, err := os.FindProcess(pid)
	if err != nil {
		return
	}
	proc.Terminate(p)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && isOurs(pid) {
		time.Sleep(100 * time.Millisecond)
	}
	if isOurs(pid) {
		_ = p.Kill()
	}
}

func (m *Manager) markLost() {
	paths, _ := filepath.Glob(filepath.Join(jobsDir(m.home), "*", "job.json"))
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		rec, err := jsonx.DecodeObject(data)
		if err != nil {
			continue
		}
		state := jsonx.Str(rec, "state")
		if !isLive(state) && state != "stopping" {
			continue
		}
		if pid, ok := jsonx.Int(rec, "pid"); ok {
			killOrphan(int(pid))
		}
		rec.Set("state", "lost")
		if out, err := jsonx.Marshal(rec); err == nil {
			_ = os.WriteFile(p, out, 0o600)
		}
		_ = os.Remove(filepath.Join(filepath.Dir(p), "pause.flag"))
	}
}

func (m *Manager) recent() []any {
	paths, _ := filepath.Glob(filepath.Join(jobsDir(m.home), "*", "job.json"))
	sort.Sort(sort.Reverse(sort.StringSlice(paths)))
	if len(paths) > recentN {
		paths = paths[:recentN]
	}
	out := []any{}
	for _, p := range paths {
		id := filepath.Base(filepath.Dir(p))
		m.mu.Lock()
		job := m.jobs[id]
		m.mu.Unlock()
		if job != nil {
			out = append(out, job.Describe())
			continue
		}
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		if rec, err := jsonx.DecodeObject(data); err == nil {
			out = append(out, rec)
		}
	}
	return out
}

func (m *Manager) live() *Job {
	m.mu.Lock()
	cur := m.current
	m.mu.Unlock()
	if cur != nil {
		if s := cur.State(); isLive(s) || s == "stopping" {
			return cur
		}
	}
	return nil
}

// Status 当前任务和最近 10 个任务。
func (m *Manager) Status() *jsonx.Object {
	var cur any
	if j := m.live(); j != nil {
		cur = j.Describe()
	}
	return jsonx.NewObject().Set("current", cur).Set("recent", m.recent())
}

// Start 开一个任务。
func (m *Manager) Start(kind string, params *jsonx.Object, llmEnv map[string]string) (*jsonx.Object, error) {
	k, ok := Kinds[kind]
	if !ok {
		return nil, clierr.Usage("bad_args", "没有这种任务："+kind, "可选："+strings.Join(KindNames, "、"))
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if cur := m.current; cur != nil {
		if s := cur.State(); isLive(s) || s == "stopping" {
			return nil, clierr.New("busy", "已经有任务在跑："+cur.Kind.Summary(cur.Params), "同一时间只能跑一个对局或扫描；等它结束，或 hiro run stop")
		}
	}
	rt, err := m.Resolve(kind)
	if err != nil {
		return nil, err
	}
	job, err := NewJob(m.home, k, params, rt, llmEnv, m.exited)
	if err != nil {
		return nil, err
	}
	m.jobs[job.ID] = job
	m.current = job
	if err := job.Start(); err != nil {
		job.setState("failed")
		job.Emit("failed", "任务没能启动："+err.Error())
		job.save()
		m.current = nil
		return nil, clierr.New("start_failed", "任务没能启动："+err.Error(), "运行 hiro doctor 检查环境")
	}
	return jsonx.NewObject().Set("job", job.Describe()), nil
}

// exited 是任务结束时的收尾（在结束事件之前）：对局出成绩摘要，扫描结果导入信息库。
func (m *Manager) exited(job *Job) {
	if produceKinds[job.Kind.Name] && len(report.Segments(report.TracesDir(), job.ID)) > 0 {
		exams := report.ExamScores(job.ID)
		if line := report.ScoreLine(exams); line != "" {
			job.Emit("report", line, "exams", exams)
		}
	}
	if job.Kind.Output != "" {
		ingest(job)
	}
}

// ingest 把扫描任务的 out.json 导入信息库，并在事件流里记一条。
func ingest(job *Job) {
	data, err := os.ReadFile(filepath.Join(job.Dir, "out.json"))
	if err != nil {
		job.Emit("import_failed", "扫描没有产出结果，信息库没有更新（看上面的日志）")
		return
	}
	inv, err := jsonx.DecodeObject(data)
	if err != nil {
		job.Emit("import_failed", "扫描结果导入失败："+err.Error())
		return
	}
	r, err := info.ImportInventory(job.Kind.Output, inv, "scan:"+job.ID)
	if err != nil {
		job.Emit("import_failed", "扫描结果导入失败："+err.Error())
		return
	}
	n, _ := jsonx.Int(r, "count")
	job.Emit("imported", "已更新信息库："+itoa(n)+" 条", "kind", job.Kind.Output, "count", n)
}

func itoa(n int64) string { b, _ := jsonx.Marshal(n); return string(b) }

func (m *Manager) job(id string) *Job {
	m.mu.Lock()
	defer m.mu.Unlock()
	if id != "" {
		return m.jobs[id]
	}
	return m.current
}

// Events 读一个任务 seq >= since 的事件；id 空就是当前任务。服务重启前的任务直接读文件。
func (m *Manager) Events(id string, since int64) (*jsonx.Object, error) {
	job := m.job(id)
	if job == nil && id != "" {
		if report.CheckJob(id) != nil {
			return nil, clierr.New("not_found", "没有这个任务："+id, "hiro run status 看最近的任务")
		}
		dir := filepath.Join(jobsDir(m.home), id)
		evPath := filepath.Join(dir, "events.jsonl")
		if _, err := os.Stat(evPath); err != nil {
			return nil, clierr.New("not_found", "没有这个任务："+id, "hiro run status 看最近的任务")
		}
		var state any
		if data, err := os.ReadFile(filepath.Join(dir, "job.json")); err == nil {
			if rec, err := jsonx.DecodeObject(data); err == nil {
				state, _ = rec.Get("state")
			}
		}
		return jsonx.NewObject().Set("job", id).Set("state", state).Set("events", readEvents(evPath, since)), nil
	}
	if job == nil {
		return nil, clierr.New("no_job", "现在没有任务", "hiro run start 开一个")
	}
	return jsonx.NewObject().Set("job", job.ID).Set("state", job.State()).Set("events", job.Events(since)), nil
}

// Control 暂停 / 接手 / 继续 / 停止。
func (m *Manager) Control(action, id string) (*jsonx.Object, error) {
	job := m.job(id)
	if job == nil || !isLive(job.State()) {
		return nil, clierr.New("no_job", "现在没有在跑的任务", "hiro run status 看状态")
	}
	if (action == "pause" || action == "takeover") && !job.Kind.Pausable {
		return nil, clierr.New("not_pausable", "「"+job.Kind.Summary(job.Params)+"」不能暂停或接手，只能停止",
			"要自己操作就先 hiro run stop（不会放弃培育）")
	}
	switch action {
	case "pause":
		job.Pause("用户暂停", "paused")
	case "takeover":
		job.Pause("用户接手：自动操作停在检查点，不再点击", "takeover")
	case "resume":
		job.Resume()
	case "stop":
		job.setState("stopping")
		go job.Stop()
	}
	return jsonx.NewObject().Set("job", job.Describe()), nil
}

// StopCurrent 停掉正在跑的任务（服务退出时），等它结束。
func (m *Manager) StopCurrent() {
	if j := m.live(); j != nil {
		j.Stop()
	}
}
