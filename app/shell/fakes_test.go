package shell

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// memThreads 是内存里的对话存储。
type memThreads struct {
	mu    sync.Mutex
	files map[string]json.RawMessage
	order []string
}

func newMem(raw ...string) *memThreads {
	m := &memThreads{files: map[string]json.RawMessage{}}
	for _, r := range raw {
		var t struct{ ID string }
		_ = json.Unmarshal([]byte(r), &t)
		m.files[t.ID] = json.RawMessage(r)
		m.order = append(m.order, t.ID)
	}
	return m
}

func (m *memThreads) List() []json.RawMessage {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []json.RawMessage
	for _, id := range m.order {
		if b, ok := m.files[id]; ok {
			out = append(out, b)
		}
	}
	return out
}

func (m *memThreads) Save(id string, b json.RawMessage) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.files[id]; !ok {
		m.order = append([]string{id}, m.order...)
	}
	m.files[id] = append(json.RawMessage{}, b...)
	return nil
}

func (m *memThreads) Delete(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.files, id)
	return nil
}

// fakeCall 按命令前几个词返回预置信封，并记下调过什么。
type fakeCall struct {
	mu    sync.Mutex
	calls [][]string
	resp  map[string]hiroapi.Result
}

func (f *fakeCall) Call(_ context.Context, args []string) hiroapi.Result {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, append([]string{}, args...))
	for n := len(args); n > 0; n-- {
		if r, ok := f.resp[strings.Join(args[:n], " ")]; ok {
			return r
		}
	}
	return hiroapi.Result{OK: true, Data: json.RawMessage(`{}`)}
}

func (f *fakeCall) called(prefix string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.calls {
		if strings.HasPrefix(strings.Join(c, " "), prefix) {
			return true
		}
	}
	return false
}

func ok(data string) hiroapi.Result { return hiroapi.Result{OK: true, Data: json.RawMessage(data)} }

var fixedNow = time.Date(2026, 11, 24, 1, 30, 0, 0, time.Local)

func newTestApp(threads *memThreads, call *fakeCall, ask Asker, reg *plugin.Registry) (*App, *SyncRunner) {
	if reg == nil {
		reg = &plugin.Registry{}
	}
	run := &SyncRunner{}
	a := New(Deps{Call: call.Call, Ask: ask, Threads: threads, Run: run, Home: "/tmp/hiro-test", Now: func() time.Time { return fixedNow }}, reg)
	return a, run
}

type hiroapiResult = hiroapi.Result

func pendingResult(id, summary string) hiroapi.Result {
	d, _ := json.Marshal(map[string]any{"pending": map[string]any{"id": id, "command": "hiro run start daily", "summary": summary, "expires_at": 4102444800}})
	return hiroapi.Result{OK: true, Data: d, Exit: hiroapi.PendingExit, Pending: &hiroapi.Pending{ID: id, Command: "hiro run start daily", Summary: summary, ExpiresAt: 4102444800}}
}
