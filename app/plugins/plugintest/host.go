// Package plugintest 是插件测试用的假宿主。
package plugintest

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// Host 是假的 plugin.Host：工具层按命令前几个词返回预置信封，Go 同步执行，其它动作记下来。
type Host struct {
	mu       sync.Mutex
	Resp     map[string]hiroapi.Result
	Calls    [][]string
	RunArgs_ [][]string
	Asked    []string
	Posted   []plugin.Card
	Panel    [2]string
	Closed   bool
	Jobs     map[string]*plugin.JobView
	Live     string
	Chat     map[string]bool // 在对话里开的局
	Busy_    bool            // 助手正在回复
	HomeDir  string
	At       time.Time
}

// New 建一个假宿主；resp 的键是命令前几个词（"run status"）。
func New(resp map[string]string) *Host {
	h := &Host{Resp: map[string]hiroapi.Result{}, Jobs: map[string]*plugin.JobView{}, At: time.Date(2026, 11, 24, 1, 30, 0, 0, time.Local)}
	for k, v := range resp {
		h.Resp[k] = hiroapi.Result{OK: true, Data: json.RawMessage(v)}
	}
	return h
}

func (h *Host) Call(_ context.Context, args []string) hiroapi.Result {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.Calls = append(h.Calls, append([]string{}, args...))
	for n := len(args); n > 0; n-- {
		if r, ok := h.Resp[strings.Join(args[:n], " ")]; ok {
			return r
		}
	}
	return hiroapi.Result{OK: false, Error: &hiroapi.CliError{Code: "not_found", Message: "假宿主没有 " + strings.Join(args, " ")}}
}

// Called 是有没有调过以 prefix 开头的命令。
func (h *Host) Called(prefix string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, c := range h.Calls {
		if strings.HasPrefix(strings.Join(c, " "), prefix) {
			return true
		}
	}
	return false
}

func (h *Host) Go(work func() func()) {
	if apply := work(); apply != nil {
		apply()
	}
}
func (h *Host) Ask(text string)                    { h.Asked = append(h.Asked, text) }
func (h *Host) Post(text string, card plugin.Card) { h.Posted = append(h.Posted, card) }
func (h *Host) RunArgs(label string, args []string) {
	h.RunArgs_ = append(h.RunArgs_, append([]string{label}, args...))
}
func (h *Host) OpenPanel(id, route string) { h.Panel = [2]string{id, route} }
func (h *Host) ClosePanel()                { h.Closed = true }
func (h *Host) Job(id string) *plugin.JobView {
	v, ok := h.Jobs[id]
	if !ok {
		v = &plugin.JobView{}
		h.Jobs[id] = v
	}
	return v
}
func (h *Host) LiveJob() string { return h.Live }

// Busy 是 Busy_。
func (h *Host) Busy() bool { return h.Busy_ }

// ChatJob 是 Chat 里记着的局。
func (h *Host) ChatJob(id string) bool { return h.Chat[id] }
func (h *Host) Home() string           { return h.HomeDir }
func (h *Host) Now() time.Time         { return h.At }

var _ plugin.Host = (*Host)(nil)

// Panel 把插件面板包成和宿主里一样的竖排容器（测试用）。
func PanelView(f func(c *ui.Context)) func(c *ui.Context) {
	return func(c *ui.Context) {
		ui.Column(c).Fill().Padding(14, 16).Gap(12).Children(func() { f(c) })
	}
}

// ClickButton 按读屏看到的按钮名字点（名字和别的文字重了时用，比 Tester.Click 准）。
func ClickButton(tt *ui.Tester, name string) error {
	a, ok := tt.AccessByRole(ui.RoleButton, name)
	if !ok {
		return fmt.Errorf("没有叫 %q 的按钮", name)
	}
	b := a.Bounds
	tt.ClickAt(b.X+b.W/2, b.Y+b.H/2)
	return nil
}
