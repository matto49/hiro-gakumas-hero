package main

import (
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/extplugin"
	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/cockpit"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/library"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plan"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/settings"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/start"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/usage"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

// appDeps 是搭原生界面要的东西；测试里换掉 Call / Run / Ready。
type appDeps struct {
	Home      string
	Call      hiroapi.Caller
	Hiro      *Hiro
	Assistant *Assistant
	Bootstrap *Bootstrap
	Run       shell.Runner
	// Ready 盖掉 Bootstrap 的「环境装好没」（测试用）；nil 时用 Bootstrap.Status。
	Ready func(ctx context.Context) bool
	// Web 是侧边面板的网页视图（nil：测试里不接）。
	Web *webPanels
}

// buildApp 注册内置插件，搭出主界面。
func buildApp(d appDeps) *shell.App {
	reg := &plugin.Registry{}
	mgr := &extplugin.Manager{Home: d.Home, Reg: reg}
	cache := &settingsCache{a: d.Assistant}
	var app *shell.App
	sd := settingsDeps(d, cache)
	sd.PluginsPanel = mgr.Panel
	sd.OnWizardDone = func() { // R11：第一次用，自己扫一遍从没扫过的库存
		if app != nil {
			app.FirstScan()
		}
	}
	set := settings.New(sd)
	reg.Add(start.New())
	reg.Add(plan.New())
	reg.Add(library.New())
	reg.Add(cockpit.New())
	reg.Add(usage.New())
	reg.Add(set.Plugin())
	mgr.Load() // 同意过的外部插件排在内置插件后面
	plugins = mgr
	if d.Web != nil {
		d.Web.hook(mgr.Dir)
	}
	a := d.Assistant
	a.ExtTools = func() []ExtTool {
		var out []ExtTool
		for _, t := range mgr.AgentTools() {
			spec := ToolSpec{Name: t.Name, ToolName: t.Name, Risk: "read", Help: "（插件「" + t.Plugin + "」）" + t.Tool.Help, Agent: true}
			for _, arg := range t.Tool.Args {
				spec.Args = append(spec.Args, ToolArg{Name: arg.Name, Help: arg.Help, Type: arg.Type, Required: arg.Required, Choices: arg.Choices})
			}
			run := t.Run
			out = append(out, ExtTool{Spec: spec, Run: func(ctx context.Context, args map[string]any) Result { return run(ctx, app, args) }})
		}
		return out
	}
	app = shell.New(shell.Deps{
		Call:    d.Call,
		Threads: &Threads{Home: d.Home},
		Run:     d.Run,
		Home:    d.Home,
		Ask: func(ctx context.Context, history []shell.Turn, text, extra string, onTool func(string, string)) (shell.Reply, error) {
			h := make([]Turn, len(history))
			for i, t := range history {
				h[i] = Turn{Role: t.Role, Content: t.Content}
			}
			r, err := a.AskWith(ctx, h, text, extra, onTool)
			return shell.Reply{Text: r.Text, Cards: jsonCards(r.Cards)}, err
		},
		HasModel:   func() bool { return cache.hasModel() },
		Gate:       set.Gate,
		Permission: func() string { return cache.Get().Permission },
		SetPermission: func(level string) error {
			s := cache.Get()
			s.Permission = level
			if level == shell.PermAll {
				s.AllowAllConfirmed = true
			}
			return cache.Save(s, "")
		},
		AllowAllConfirmed: func() bool { return cache.Get().AllowAllConfirmed },
	}, reg)
	return app
}

// plugins 是外部插件管理（hiroplug:// 协议找插件目录用）。
var plugins *extplugin.Manager

// jsonCards 把 agent 回的卡片（里面有 *Pending、*CliError 这类结构体）变成和存盘一样的 JSON 形状，界面只认这种。
func jsonCards(in []map[string]any) []plugin.Card {
	out := make([]plugin.Card, 0, len(in))
	for _, c := range in {
		var m map[string]any
		convert(c, &m)
		if m != nil {
			out = append(out, m)
		}
	}
	return out
}

// settingsCache 缓存 settings.json（界面每帧都要问）：2 秒内不重读，保存后立刻失效；命令行 agent 找没找到也一起缓存。
type settingsCache struct {
	a    *Assistant
	mu   sync.Mutex
	val  Settings
	at   time.Time
	look map[string]bool
}

const settingsTTL = 2 * time.Second

func (c *settingsCache) Get() Settings {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.at.IsZero() || time.Since(c.at) > settingsTTL {
		c.val, c.at, c.look = c.a.Settings(), time.Now(), nil
	}
	return c.val
}

func (c *settingsCache) Save(s Settings, key string) error {
	err := c.a.Save(s, key)
	c.mu.Lock()
	c.at = time.Time{}
	c.mu.Unlock()
	return err
}

// hasModel 是现在能不能直接和 agent 说话。
func (c *settingsCache) hasModel() bool {
	s := c.Get()
	switch s.Provider {
	case "none":
		return false
	case "codex", "claude":
		c.mu.Lock()
		defer c.mu.Unlock()
		if c.look == nil {
			c.look = map[string]bool{}
		}
		ok, seen := c.look[s.Provider]
		if !seen {
			_, err := exec.LookPath(s.Provider)
			ok = err == nil
			c.look[s.Provider] = ok
		}
		return ok
	}
	return s.HasKey
}

func convert(from, to any) {
	b, _ := json.Marshal(from)
	_ = json.Unmarshal(b, to)
}

func settingsDeps(d appDeps, cache *settingsCache) settings.Deps {
	a, boot := d.Assistant, d.Bootstrap
	return settings.Deps{
		Settings: func() settings.Settings {
			var s settings.Settings
			convert(cache.Get(), &s)
			return s
		},
		Save: func(s settings.Settings, key string) error {
			var m Settings
			convert(s, &m)
			return cache.Save(m, key)
		},
		Providers: func() []settings.Preset {
			out := make([]settings.Preset, len(Presets))
			for i, p := range Presets {
				out[i] = settings.Preset{ID: p.ID, BaseURL: p.BaseURL, DefaultModel: p.DefaultModel, KeyURL: p.KeyURL}
			}
			return out
		},
		Models: a.Models,
		Test: func(ctx context.Context) (bool, string) {
			r := a.Test(ctx)
			return r.OK, r.Message
		},
		BootStatus: func(ctx context.Context) settings.BootStatus {
			st := boot.Status(ctx)
			out := settings.BootStatus{Ready: st.Ready}
			if d.Ready != nil {
				out.Ready = d.Ready(ctx)
			}
			for _, s := range st.Steps {
				out.Steps = append(out.Steps, s.Title)
			}
			if st.Error != nil {
				out.Err = st.Error.Message + "。" + st.Error.Hint
			}
			return out
		},
		BootRun: func(ctx context.Context, onLine func(string)) error {
			return boot.RunLines(ctx, func(p Progress) { onLine(p.Line) })
		},
		BootReset: boot.Reset,
		SetSecret: func(ctx context.Context, name, value string) hiroapi.Result {
			if d.Hiro == nil {
				return hiroapi.Result{Error: &hiroapi.CliError{Code: "error", Message: errors.New("工具层不可用").Error()}}
			}
			return d.Hiro.SetSecret(ctx, name, value)
		},
	}
}
