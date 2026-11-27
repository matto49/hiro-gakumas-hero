package extplugin

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// Approval 是用户安装时同意了什么（acts 变了要重新同意）。
type Approval struct {
	Version string   `json:"version"`
	Acts    []string `json:"acts"`
	Tools   bool     `json:"tools"`
	Context bool     `json:"context"`
}

// Covers 是这份同意还管不管得住这个清单（acts 一样、要的不多于同意的）。
func (a Approval) Covers(m *Manifest) bool {
	if strings.Join(a.Acts, "\n") != strings.Join(m.Acts, "\n") {
		return false
	}
	if len(m.Contributes.Tools) > 0 && !a.Tools {
		return false
	}
	if m.Contributes.Context && !a.Context {
		return false
	}
	return true
}

// screen 是一个面板页或一张卡上插件画的东西。
type screen struct {
	tree    *Node
	state   json.RawMessage
	err     string
	v       view
	refresh time.Duration
	next    time.Time
	session uint64
	edits   []screenEdit
}

type screenEdit struct{ id, value string }

// Ext 是一个已同意的外部插件。
type Ext struct {
	m      *Manifest
	ap     Approval
	home   string
	c      *client
	env    []string
	closed atomic.Bool

	mu      sync.Mutex
	h       plugin.Host
	screens map[string]*screen
}

// New 建一个外部插件（进程用到时才起）。
func New(m *Manifest, ap Approval, home string) *Ext {
	return newExt(m, ap, home, nil)
}

func newExt(m *Manifest, ap Approval, home string, env []string) *Ext {
	e := &Ext{m: m, ap: ap, home: home, screens: map[string]*screen{}, env: append([]string(nil), env...)}
	argv := append([]string{}, m.Command...)
	e.c = newClient(m.Dir, argv, append(pluginEnv(home, m.ID), e.env...), e.serve)
	return e
}

// pluginEnv 是插件进程的环境：只带起程序要的系统变量和 HIRO_HOME / HIRO_PLUGIN_ID；模型 Key 之类一概不带。
func pluginEnv(home, id string) []string {
	env := []string{"HIRO_HOME=" + home, "HIRO_PLUGIN_ID=" + id, "PYTHONUTF8=1"}
	for _, k := range []string{"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "EXTPLUGIN_FAKE",
		"SYSTEMROOT", "SystemRoot", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"} {
		if v, ok := os.LookupEnv(k); ok {
			env = append(env, k+"="+v)
		}
	}
	return env
}

// Manifest 是插件清单。
func (e *Ext) Manifest() *Manifest { return e.m }

// Close 关掉插件进程。
func (e *Ext) Close() {
	e.closed.Store(true)
	e.c.Close()
}

func (e *Ext) host(h plugin.Host) {
	e.mu.Lock()
	if h != nil {
		e.h = h
	}
	e.mu.Unlock()
}

// serve 处理插件发来的请求：只有 hiro.call。
func (e *Ext) serve(ctx context.Context, method string, params json.RawMessage) (any, error) {
	if method != "hiro.call" {
		return nil, fmt.Errorf("没有这个方法：%s", method)
	}
	var p struct {
		Args []string `json:"args"`
	}
	if err := json.Unmarshal(params, &p); err != nil {
		return nil, err
	}
	e.mu.Lock()
	h := e.h
	e.mu.Unlock()
	deny := hiroapi.Result{Error: &hiroapi.CliError{Code: "not_allowed", Message: "插件不能调这条命令：" + strings.Join(p.Args, " "),
		Hint: "会动游戏的命令要写进 plugin.json 的 acts 并经用户同意；确认、取消、账号密码永远只能由用户在界面上操作"}}
	if h == nil || forbiddenAct(p.Args) {
		return deny, nil
	}
	if !ActAllowed(e.ap.Acts, p.Args) && !ReadAllowed(p.Args) {
		return deny, nil
	}
	r := h.Call(ctx, p.Args)
	if r.Pending != nil {
		pd := *r.Pending
		name := e.m.Name
		h.Go(func() func() {
			return func() {
				h.Post("插件「"+name+"」想要："+pd.Summary, plugin.Card{"type": "confirm", "status": "waiting", "from": name,
					"pending": pd.Map()})
			}
		})
	}
	return r, nil
}

// CallTool 让插件跑它的一个工具（agent 调用）。
func (e *Ext) CallTool(ctx context.Context, h plugin.Host, name string, args map[string]any) hiroapi.Result {
	e.host(h)
	var r hiroapi.Result
	if err := e.c.Call(ctx, "tool.call", map[string]any{"name": name, "args": args}, &r); err != nil {
		return hiroapi.Result{Error: &hiroapi.CliError{Code: "plugin_error", Message: "插件「" + e.m.Name + "」出错：" + err.Error()}}
	}
	return r
}

// Tools 是插件给 agent 的工具（没同意时为空）。
func (e *Ext) Tools() []Tool {
	if !e.ap.Tools {
		return nil
	}
	return e.m.Contributes.Tools
}

type rendered struct {
	Tree      *Node           `json:"tree"`
	State     json.RawMessage `json:"state"`
	RefreshMS *int            `json:"refreshMs,omitempty"`
}

func (s *screen) needsRender(now time.Time, visiblePanel bool, session uint64) bool {
	if s.v.busy != "" {
		return false
	}
	return (s.tree == nil && s.err == "") ||
		(visiblePanel && s.refresh > 0 && (session != s.session || !now.Before(s.next)))
}

func (s *screen) apply(r rendered, now time.Time, panel bool) {
	if r.Tree != nil {
		s.tree = r.Tree
	}
	if r.State != nil {
		s.state = r.State
	}
	if panel && r.RefreshMS != nil {
		s.refresh = 0
		if *r.RefreshMS > 0 {
			s.refresh = time.Duration(min(30_000, max(1_000, *r.RefreshMS))) * time.Millisecond
		}
	}
	s.next = now.Add(s.refresh)
}

func (e *Ext) screen(key string) *screen {
	e.mu.Lock()
	defer e.mu.Unlock()
	s, ok := e.screens[key]
	if !ok {
		s = &screen{v: view{dir: e.m.Dir, home: e.home}}
		e.screens[key] = s
	}
	return s
}

// draw 画一个页 / 一张卡：没有树时向插件要（method / params），有树时画手上这棵；事件交给插件换新树。
func (e *Ext) draw(c *ui.Context, h plugin.Host, key, method string, params map[string]any) {
	if e.closed.Load() {
		return
	}
	panel := strings.HasPrefix(key, "panel-")
	if panel && c.WindowHidden() {
		return
	}
	e.host(h)
	s := e.screen(key)
	var session uint64
	var panelSession func() uint64
	if panelHost, ok := h.(interface{ PanelSession() uint64 }); panel && ok {
		panelSession = panelHost.PanelSession
		session = panelSession()
		if session == 0 {
			return
		}
	}
	if s.v.busy != "" && s.session != session {
		kit.Note(c, "正在问插件「"+e.m.Name+"」…")
		return
	}
	if panel && s.session != 0 && s.session != session && s.refresh > 0 {
		// A reopened live panel must obtain a current view before exposing its old data or controls.
		s.tree, s.state, s.err, s.v.inputs = nil, nil, "", nil
	}
	current := func() bool {
		return !e.closed.Load() && (panelSession == nil || panelSession() == session)
	}
	var sendEvent func(id, value string)
	request := func(rpcMethod, busy string, rpcParams map[string]any) {
		s.session = session
		s.v.busy = busy
		h.Go(func() func() {
			var r rendered
			err := e.c.Call(context.Background(), rpcMethod, rpcParams, &r)
			return func() {
				s.v.busy = ""
				if !current() {
					// 旧会话的响应与尚未发出的编辑都不进入重新打开的面板。
					s.tree, s.state, s.err, s.v.inputs, s.edits = nil, nil, "", nil, nil
					return
				}
				if err != nil {
					s.err = err.Error()
					s.next = h.Now().Add(s.refresh)
				} else {
					if rpcMethod != "event" {
						s.err = ""
					}
					s.apply(r, h.Now(), panel)
				}
				if len(s.edits) > 0 {
					edit := s.edits[0]
					s.edits = s.edits[1:]
					sendEvent(edit.id, edit.value)
				}
			}
		})
	}
	sendEvent = func(id, value string) {
		p := map[string]any{"id": id, "value": value, "state": s.state}
		for k, v := range params {
			p[k] = v
		}
		request("event", id, p)
	}
	if s.needsRender(h.Now(), panel, session) {
		request(method, "render", params)
	} else if panel && s.refresh > 0 && s.v.busy == "" {
		// 自动刷新：界面空闲时不会自己重画，到期了也没人来画这一帧，按下次到期时间要一帧（CodeRabbit #85）
		if d := s.next.Sub(h.Now()); d > 0 {
			c.After(d)
		}
	}
	switch {
	case s.err != "":
		ui.Text(c, "插件「"+e.m.Name+"」出错："+s.err).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
		if kit.Ghost(c, "重试").Key(key + "-retry").Clicked() {
			s.err = ""
			s.tree = nil
		}
		return
	case s.tree == nil:
		kit.Note(c, "正在问插件「"+e.m.Name+"」…")
		return
	}
	s.v.onEvent = func(id, value string, edit bool) {
		if !current() {
			return
		}
		if s.v.busy != "" {
			if edit { // 卡片、插件设置页也排队（原来只排面板的，别处请求还没回来时改的控件会被静默丢掉；CodeRabbit #85）
				s.edits = append(s.edits, screenEdit{id, value})
			}
			return
		}
		sendEvent(id, value)
	}
	s.v.render(c, *s.tree, key)
}

// WebPanel 画网页面板（main 里接上网页视图，见 kit.WebPanel）；nil 时只给路径。
var WebPanel func(c *ui.Context, h plugin.Host, file string, untrusted bool)

// Plugin 把外部插件包成和内置插件一样的 plugin.Plugin。
func (e *Ext) Plugin() *plugin.Plugin {
	m := e.m
	p := &plugin.Plugin{ID: m.ID, Name: m.Name, Icon: kit.IcPuzzle}
	if m.Icon != "" {
		if path := safePath(m.Dir, "", m.Icon); path != "" {
			if b, err := os.ReadFile(path); err == nil {
				if svg, err := ui.ParseSVG(b); err == nil {
					p.Icon = svg
				}
			}
		}
	}
	for _, cmd := range m.Contributes.Commands {
		p.Commands = append(p.Commands, plugin.Command{Name: cmd.Name, Hint: cmd.Hint, Route: cmd.Panel})
	}
	for _, ch := range m.Contributes.Chips {
		p.Chips = append(p.Chips, plugin.Chip{Label: ch.Label, Say: ch.Say})
	}
	if len(m.Contributes.Cards) > 0 {
		p.Cards = map[string]plugin.CardView{}
		for _, typ := range m.Contributes.Cards {
			full := m.ID + ":" + typ
			p.Cards[full] = func(c *ui.Context, h plugin.Host, card plugin.Card) {
				key := fmt.Sprintf("card-%s-%p", typ, card)
				e.draw(c, h, key, "card.render", map[string]any{"type": typ, "data": card["data"]})
			}
		}
	}
	if len(m.Contributes.Panels) > 0 {
		p.Nav = []plugin.NavItem{{Label: m.Name, Icon: p.Icon, Route: m.Contributes.Panels[0].Route}}
		p.Panel = func(c *ui.Context, h plugin.Host, route string) {
			for _, pn := range m.Contributes.Panels {
				if pn.Route != route {
					continue
				}
				if pn.Kind == "web" {
					file := safePath(m.Dir, "", pn.File)
					if WebPanel != nil {
						WebPanel(c, h, file, true)
					} else {
						kit.Note(c, "网页面板："+file)
					}
					return
				}
				e.draw(c, h, "panel-"+route, "panel.render", map[string]any{"route": route})
				return
			}
			kit.Note(c, "插件「"+m.Name+"」没有这一页："+route)
		}
	}
	if m.Contributes.Settings {
		p.Settings = func(c *ui.Context, h plugin.Host) {
			e.draw(c, h, "settings", "panel.render", map[string]any{"route": "settings"})
		}
	}
	if m.Contributes.Context && e.ap.Context {
		p.ContextTitle = m.Name
		p.Context = func(ctx context.Context, h plugin.Host) string {
			e.host(h)
			var s string
			if e.c.Call(ctx, "context.get", map[string]any{}, &s) != nil {
				return ""
			}
			return s
		}
	}
	return p
}
