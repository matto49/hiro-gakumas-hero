// Package shell 是初星助手的主界面：侧栏、对话、输入框、确认卡、侧边面板（设计稿 §4、§6）。
package shell

import (
	"context"

	"encoding/json"
	"github.com/egoist/mygo/ui"
	"strings"
	"sync/atomic"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/usagelog"
)

// Turn 是交给 agent 的一句历史。
type Turn struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// Reply 是 agent 这一轮的回答。
type Reply struct {
	Text  string
	Cards []plugin.Card
}

// Asker 问 agent 一句：extra 是这一轮的「现在的情况」，onTool 在每次调工具时被调用（可能在后台 goroutine）。
type Asker func(ctx context.Context, history []Turn, text, extra string, onTool func(name, summary string)) (Reply, error)

// Deps 是 shell 用到的外部东西，测试里全换成假的。
type Deps struct {
	Call    hiroapi.Caller
	Ask     Asker // nil：没配模型，只能用快捷按钮和 "/" 命令
	Threads ThreadStore
	Run     Runner
	Home    string
	Now     func() time.Time
	// HasModel 是现在配没配模型（设置改了随时变）；nil 时看 Ask 是否为 nil。
	HasModel func() bool
	// Gate 画在主界面之前（第一次使用）：返回 true 表示它画了整个窗口。
	Gate func(c *ui.Context, h plugin.Host) bool
	// Permission 是用户选的操作权限档（PermAsk / PermRead / …）；nil 或空是「允许读操作」。
	Permission func() string
}

// App 是主界面的全部状态。只在界面线程里改。
type App struct {
	d       Deps
	reg     *plugin.Registry
	threads []*Thread
	cur     *Thread
	busy    bool
	tools   []string // 这一轮已调过的工具（回复到之前先显示）
	ctx     context.Context

	input string
	ui    viewState

	jobs     map[string]*plugin.JobView
	live     string // 活着的任务编号
	polling  bool
	hidden   atomic.Bool // 窗口隐藏（轮询降速）
	busyJobs atomic.Bool // 有活任务或没读过的任务（每秒查；否则 5 秒）
	notified map[string]bool
	ended    map[string]bool // 已调过 OnJobEnd 的局
	started  map[string]bool // 本会话里从确认卡开的局
}

// New 建主界面，读出已存的对话。
func New(d Deps, reg *plugin.Registry) *App {
	if d.Now == nil {
		d.Now = time.Now
	}
	a := &App{d: d, reg: reg, ctx: context.Background()}
	a.threads = loadThreads(d.Threads)
	if len(a.threads) > 0 {
		a.cur = a.threads[0]
	}
	return a
}

// Registry 是插件注册表。
func (a *App) Registry() *plugin.Registry { return a.reg }

// Threads 是全部对话（新的在前）。
func (a *App) Threads() []*Thread { return a.threads }

// Current 是当前对话（可能为 nil）。
func (a *App) Current() *Thread { return a.cur }

// Busy 是不是在等 agent 回复。
func (a *App) Busy() bool { return a.busy }

// PendingTools 是这一轮已经调过的工具（等回复时显示）。
func (a *App) PendingTools() []string { return a.tools }

// NewThread 开一个新对话（不存，直到第一句话）。
func (a *App) NewThread() *Thread {
	if a.cur != nil && len(a.cur.Messages) == 0 {
		return a.cur
	}
	t := &Thread{ID: uid("t", a.d.Now()), Title: "新对话", Messages: []*Message{}, UpdatedAt: a.d.Now().UnixMilli()}
	a.threads = append([]*Thread{t}, a.threads...)
	a.cur = t
	return t
}

// Select 切到某个对话。
func (a *App) Select(id string) {
	for _, t := range a.threads {
		if t.ID == id {
			a.cur = t
			return
		}
	}
}

// DeleteThread 删一个对话。
func (a *App) DeleteThread(id string) {
	for i, t := range a.threads {
		if t.ID == id {
			a.threads = append(a.threads[:i], a.threads[i+1:]...)
			_ = a.d.Threads.Delete(id)
			if a.cur == t {
				a.cur = nil
				if len(a.threads) > 0 {
					a.cur = a.threads[0]
				}
			}
			return
		}
	}
}

func (a *App) ensureThread() *Thread {
	if a.cur == nil {
		return a.NewThread()
	}
	return a.cur
}

func (a *App) add(role, text string, cards []plugin.Card) *Message {
	t := a.ensureThread()
	if cards == nil {
		cards = []plugin.Card{}
	}
	m := &Message{ID: uid("m", a.d.Now()), Role: role, Text: text, Cards: cards, At: a.d.Now().UnixMilli()}
	if role == "user" && len(t.Messages) == 0 {
		t.Title = titleFrom(text)
	}
	t.Messages = append(t.Messages, m)
	t.UpdatedAt = m.At
	// 同一条回复里还有在等的选择卡（还没选偶像）时，确认卡不自动开始（CodeRabbit #79）
	choosing := false
	for _, c := range cards {
		if c["type"] == "choice" && c["status"] == "waiting" {
			choosing = true
		}
	}
	for _, c := range cards {
		if !choosing {
			a.autoStart(m, c)
		}
	}
	// 最近用过的对话排到最前
	for i, x := range a.threads {
		if x == t && i > 0 {
			copy(a.threads[1:i+1], a.threads[:i])
			a.threads[0] = t
		}
	}
	return m
}

func (a *App) save(t *Thread) {
	if t == nil || len(t.Messages) == 0 {
		return
	}
	b, err := json.Marshal(t)
	if err == nil {
		_ = a.d.Threads.Save(t.ID, b)
	}
}

func history(t *Thread) []Turn {
	var out []Turn
	for _, m := range t.Messages {
		if strings.TrimSpace(m.Text) == "" {
			continue
		}
		role := "user"
		if m.Role == "agent" {
			role = "assistant"
		}
		out = append(out, Turn{Role: role, Content: m.Text})
	}
	return out
}

func (a *App) hasModel() bool {
	if a.d.Ask == nil {
		return false
	}
	return a.d.HasModel == nil || a.d.HasModel()
}

// NoModelText 是没配模型时的回答。
const NoModelText = "还没配模型，初星助手现在只能做输入框上面那几件事，或者用「/」命令。到设置里配好模型，就可以直接说话了。"

// Send 发一句话：配了模型交给 agent，没配就提示。
func (a *App) Send(text string) {
	text = strings.TrimSpace(text)
	if text == "" || a.busy {
		return
	}
	t := a.ensureThread()
	hist := history(t)
	a.add("user", text, nil)
	if !a.hasModel() {
		a.add("agent", NoModelText, nil)
		a.save(t)
		return
	}
	a.save(t)
	a.busy, a.tools = true, nil
	ask, run, reg := a.d.Ask, a.d.Run, a.reg
	snap := a.snapshotHost()
	a.d.Run.Go(func() func() {
		extra := plugin.Gather(a.ctx, reg, snap, a.d.Now(), plugin.ContextTimeout)
		var tools []string
		// 对话 id 放进 ctx：对话助手记用量时按对话归账
		reply, err := ask(usagelog.WithThread(a.ctx, t.ID), hist, text, extra, func(name, summary string) {
			line := name
			if summary != "" {
				line += " · " + summary
			}
			tools = append(tools, line)
			cp := append([]string{}, tools...)
			run.Post(func() { a.tools = cp })
		})
		return func() {
			a.busy, a.tools = false, nil
			if err != nil {
				m := a.addTo(t, "agent", "", []plugin.Card{{"type": "error", "error": map[string]any{"code": "agent", "message": err.Error()}}})
				m.Tools = tools
			} else {
				m := a.addTo(t, "agent", reply.Text, tidyCards(reply.Cards))
				m.Tools = tools
			}
			a.save(t)
		}
	})
}

// addTo 往指定对话加消息（回复回来时用户可能已经切到别的对话）。
func (a *App) addTo(t *Thread, role, text string, cards []plugin.Card) *Message {
	cur := a.cur
	a.cur = t
	m := a.add(role, text, cards)
	a.cur = cur
	return m
}

// RunArgs 直接跑一条工具层命令（快捷按钮、没配模型时），结果做成卡片；label 记成用户说的话。
func (a *App) RunArgs(label string, args []string) {
	t := a.ensureThread()
	a.add("user", label, nil)
	a.save(t)
	call := a.d.Call
	a.d.Run.Go(func() func() {
		r := call(a.ctx, args)
		return func() {
			a.addTo(t, "agent", "", CardsFor(r))
			a.save(t)
		}
	})
}

// ---- plugin.Host ----

// Call 调工具层。
func (a *App) Call(ctx context.Context, args []string) hiroapi.Result { return a.d.Call(ctx, args) }

// Go 在后台跑。
func (a *App) Go(work func() func()) { a.d.Run.Go(work) }

// Ask 以用户身份发一句话。
func (a *App) Ask(text string) { a.Send(text) }

// Post 以初星助手的身份加一条消息。
func (a *App) Post(text string, card plugin.Card) {
	t := a.ensureThread()
	var cards []plugin.Card
	if card != nil {
		cards = []plugin.Card{card}
	}
	a.add("agent", text, cards)
	a.save(t)
}

// Home 是 HIRO_HOME。
func (a *App) Home() string { return a.d.Home }

// Now 是现在。
func (a *App) Now() time.Time { return a.d.Now() }

// RunArgs 以用户身份跑一条命令（见上面）；这里满足 plugin.Host。
var _ plugin.Host = (*App)(nil)

// OpenPanel 在侧边面板打开某插件的某页。
func (a *App) OpenPanel(pluginID, route string) {
	a.ui.panelOpen, a.ui.panelPlugin, a.ui.panelRoute = true, pluginID, route
}

// snapHost 是给后台线程用的宿主：任务数据是界面线程上拍下的快照；会改界面状态的动作都排回界面线程。
type snapHost struct {
	a    *App
	jobs map[string]*plugin.JobView
	live string
	chat map[string]bool
	busy bool
}

// snapshotHost 在界面线程上拍一份快照（算上下文时用，免得后台读到正在改的 map）。
func (a *App) snapshotHost() plugin.Host {
	jobs := make(map[string]*plugin.JobView, len(a.jobs))
	for id, v := range a.jobs {
		jobs[id] = v.Copy()
	}
	chat := map[string]bool{}
	for id := range a.jobs {
		chat[id] = a.ChatJob(id)
	}
	return &snapHost{a: a, jobs: jobs, live: a.live, chat: chat, busy: a.busy}
}

func (s *snapHost) Call(ctx context.Context, args []string) hiroapi.Result {
	return s.a.d.Call(ctx, args)
}
func (s *snapHost) Go(work func() func()) { s.a.d.Run.Go(work) }
func (s *snapHost) Ask(text string)       { s.a.d.Run.Post(func() { s.a.Send(text) }) }
func (s *snapHost) Post(text string, card plugin.Card) {
	s.a.d.Run.Post(func() { s.a.Post(text, card) })
}
func (s *snapHost) RunArgs(label string, args []string) {
	s.a.d.Run.Post(func() { s.a.RunArgs(label, args) })
}
func (s *snapHost) OpenPanel(id, route string) { s.a.d.Run.Post(func() { s.a.OpenPanel(id, route) }) }
func (s *snapHost) ClosePanel()                { s.a.d.Run.Post(s.a.ClosePanel) }
func (s *snapHost) Job(id string) *plugin.JobView {
	if v, ok := s.jobs[id]; ok {
		return v
	}
	return &plugin.JobView{}
}
func (s *snapHost) LiveJob() string        { return s.live }
func (s *snapHost) Busy() bool             { return s.busy }
func (s *snapHost) ChatJob(id string) bool { return s.chat[id] }
func (s *snapHost) Home() string           { return s.a.d.Home }
func (s *snapHost) Now() time.Time         { return s.a.d.Now() }
