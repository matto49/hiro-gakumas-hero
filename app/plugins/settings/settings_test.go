package settings

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

type fake struct {
	s       Settings
	saved   []string // 存过的 Key（只记有没有，不记值）
	secrets map[string]string
	ready   bool
}

func (f *fake) deps() Deps {
	return Deps{
		Settings:  func() Settings { return f.s },
		Save:      func(s Settings, key string) error { f.s = s; f.saved = append(f.saved, key); return nil },
		Providers: func() []Preset { return []Preset{{ID: "deepseek", DefaultModel: "deepseek-chat"}, {ID: "custom"}} },
		Models:    func(context.Context, string, string, string) ([]string, error) { return []string{"deepseek-chat"}, nil },
		Test:      func(context.Context) (bool, string) { return true, "模型回答：好" },
		BootStatus: func(context.Context) BootStatus {
			return BootStatus{Ready: f.ready, Steps: []string{"下载 uv", "装 Python 环境"}}
		},
		BootRun:   func(context.Context, func(string)) error { f.ready = true; return nil },
		BootReset: func() error { return nil },
		SetSecret: func(_ context.Context, name, value string) hiroapi.Result {
			f.secrets[name] = value
			return hiroapi.Result{OK: true}
		},
	}
}

func TestGateShowsFirstRunUntilDone(t *testing.T) {
	f := &fake{secrets: map[string]string{}}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	s := New(f.deps())
	tt := ui.NewTester(func(c *ui.Context) {
		if !s.Gate(c, h) {
			ui.Text(c, "主界面")
		}
	}, 1280, 800)
	if !tt.HasText("第一次使用") || tt.HasText("主界面") {
		t.Fatalf("环境没装好时应先走第一次使用：%q", tt.Texts())
	}
	f.ready, f.s.WizardDone = true, true
	s.Recheck(h)
	tt.Frame()
	if !tt.HasText("主界面") {
		t.Errorf("装好、走完之后进主界面：%q", tt.Texts())
	}
}

func TestSaveKeyGoesToStoreNotChat(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s = Settings{Provider: "deepseek", WizardDone: true}
	h := plugintest.New(nil)
	s := New(f.deps())
	p := s.Plugin()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 1200)
	s.key = "sk-test-not-real"
	if err := plugintest.ClickButton(tt, "保存模型设置"); err != nil {
		t.Fatalf("%v %q", err, tt.Texts())
	}
	if len(f.saved) != 1 || f.saved[0] != "sk-test-not-real" {
		t.Errorf("Key 没交给存储：%d 次", len(f.saved))
	}
	if s.key != "" {
		t.Error("保存后输入框要清空")
	}
	if len(h.Posted) != 0 || len(h.Asked) != 0 {
		t.Error("Key 不能进对话")
	}
	for _, txt := range tt.Texts() {
		if strings.Contains(txt, "sk-test-not-real") {
			t.Error("界面上出现了 Key 明文")
		}
	}
}

func TestDeviceStatusFooterAndContext(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s = Settings{Provider: "deepseek", Model: "deepseek-chat", WizardDone: true}
	h := plugintest.New(map[string]string{"doctor --probe": `{"items":[{"id":"platform","ok":true,"detail":"学マス（PlayCover）：已连上"}]}`})
	s := New(f.deps())
	p := s.Plugin()
	s.Probe(h)
	tt := ui.NewTester(func(c *ui.Context) { ui.Column(c).Children(func() { p.Footer(c, h) }) }, 232, 80)
	if !tt.HasText("学マス（PlayCover） 已连上") || !tt.HasText("DeepSeek · deepseek-chat") {
		t.Errorf("侧栏底部：%q", tt.Texts())
	}
	out := p.Context(context.Background(), h)
	if !strings.Contains(out, "已连上") || !strings.Contains(out, "01:30 试连") {
		t.Errorf("上下文：%q", out)
	}
	if h.Called("doctor --probe") && strings.Count(strings.Join(flat(h.Calls), "|"), "doctor --probe") > 1 {
		t.Error("算上下文时不该再试连")
	}
}

func flat(cs [][]string) []string {
	var out []string
	for _, c := range cs {
		out = append(out, strings.Join(c, " "))
	}
	return out
}

// M5：环境状态还没回来时不画「第一次使用」（免得每次启动闪一下）。
func TestGateDoesNotFlashWizardWhileChecking(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s.WizardDone = true
	h := plugintest.New(nil)
	s := New(f.deps())
	var queued []func() func()
	hh := &deferHost{Host: h, q: &queued}
	tt := ui.NewTester(func(c *ui.Context) {
		if !s.Gate(c, hh) {
			ui.Text(c, "主界面")
		}
	}, 800, 600)
	if tt.HasText("第一次使用 · 六步") {
		t.Error("还在检查时不该画第一次使用")
	}
	for _, w := range queued {
		if a := w(); a != nil {
			a()
		}
	}
	tt.Frame()
	if !tt.HasText("主界面") {
		t.Errorf("检查完进主界面：%q", tt.Texts())
	}
}

type deferHost struct {
	*plugintest.Host
	q *[]func() func()
}

func (d *deferHost) Go(work func() func()) { *d.q = append(*d.q, work) }

// 11-25 review：操作权限挪进对话框左下角，设置页只写现在是哪一档、去哪换。
func TestPermissionPointsToComposer(t *testing.T) {
	f := &fake{s: Settings{Provider: "deepseek", WizardDone: true}, secrets: map[string]string{}, ready: true}
	p := New(f.deps()).Plugin()
	h := plugintest.New(map[string]string{})
	tt := ui.NewTester(func(c *ui.Context) { p.Panel(c, h, "") }, 760, 2400)
	tt.Frame()
	if !tt.HasText("现在是「允许读操作」。在对话框左下角点它就能换，哪些事不用确认就直接做都写在那里。") || tt.HasText("总是询问") {
		t.Errorf("设置页应只写档位和去哪换：%q", tt.Texts())
	}
}
