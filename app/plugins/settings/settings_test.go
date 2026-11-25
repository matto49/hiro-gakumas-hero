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

// G1 实测（11-26）：首次打开到开局 14 下，超过目标 12 下（N5）。
// 进「连游戏」这一步就自己试连一次；模型这一步填好 Key 直接点「下一步」：先存、再测，测通了自己进下一步。
func wizardAt(t *testing.T, f *fake, h *plugintest.Host, step int) (*S, *ui.Tester) {
	t.Helper()
	s := New(f.deps())
	tt := ui.NewTester(func(c *ui.Context) { s.Gate(c, h) }, 1280, 900)
	tt.Frame()
	s.step = step
	tt.Frame()
	tt.Frame()
	return s, tt
}

func TestWizardConnectStepProbesByItself(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`, "doctor --probe": `{"items":[{"id":"platform","ok":true,"detail":"学マス（PlayCover）：已连上"}]}`})
	_, tt := wizardAt(t, f, h, 1)
	if !h.Called("doctor --probe") || !tt.HasText("学マス（PlayCover） 已连上") {
		t.Errorf("进连游戏这一步应自己试连：%v %q", h.Calls, tt.Texts())
	}
}

func TestWizardModelNextSavesTestsAndAdvances(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	s, tt := wizardAt(t, f, h, 3)
	s.key = "sk-test-not-real"
	tt.Frame()
	if err := plugintest.ClickButton(tt, "下一步"); err != nil {
		t.Fatalf("填了 Key 下一步应能点：%v %q", err, tt.Texts())
	}
	tt.Frame()
	tt.Frame()
	if len(f.saved) != 1 || f.saved[0] != "sk-test-not-real" {
		t.Errorf("下一步要先存 Key：%v", len(f.saved))
	}
	if s.step != 4 {
		t.Errorf("测通了应自己进下一步，现在第 %d 步", s.step)
	}
}

func TestWizardModelNextStaysWhenTestFails(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	d := f.deps()
	d.Test = func(context.Context) (bool, string) { return false, "Key 不对（401）" }
	s := New(d)
	tt := ui.NewTester(func(c *ui.Context) { s.Gate(c, h) }, 1280, 900)
	tt.Frame()
	s.step, s.key = 3, "sk-bad"
	tt.Frame()
	_ = plugintest.ClickButton(tt, "下一步")
	tt.Frame()
	tt.Frame()
	if s.step != 3 || !tt.HasText("Key 不对（401）") {
		t.Errorf("测不通要留在这一步并说原因：第 %d 步 %q", s.step, tt.Texts())
	}
}

// CodeRabbit #105：切到另一家已经存过 Key 的服务商，不填新 Key 也能直接「下一步」
func TestWizardModelNextWithStoredKeyOfOtherProvider(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s = Settings{Provider: "custom", HasKey: true, HasKeys: map[string]bool{"deepseek": true, "custom": true}}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	s, tt := wizardAt(t, f, h, 3)
	s.provider = "deepseek"
	tt.Frame()
	if err := plugintest.ClickButton(tt, "下一步"); err != nil {
		t.Fatalf("这家存过 Key，下一步应能点：%v", err)
	}
	tt.Frame()
	tt.Frame()
	if s.step != 4 {
		t.Errorf("应测通后进下一步，现在第 %d 步", s.step)
	}
}

// CodeRabbit #105：测试期间改了表单，旧配置测通也不自动往下走
func TestWizardModelNoAdvanceIfFormChangedDuringTest(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	d := f.deps()
	var s *S
	d.Test = func(context.Context) (bool, string) {
		s.model = "another-model" // 测试还没回来，玩家就改了模型
		return true, "模型回答：好"
	}
	s = New(d)
	tt := ui.NewTester(func(c *ui.Context) { s.Gate(c, h) }, 1280, 900)
	tt.Frame()
	s.step, s.key = 3, "sk-test-not-real"
	tt.Frame()
	_ = plugintest.ClickButton(tt, "下一步")
	tt.Frame()
	tt.Frame()
	if s.step != 3 {
		t.Errorf("表单改过就不该自动前进，现在第 %d 步", s.step)
	}
}

// CodeRabbit #105：测试期间又粘了新 Key，旧 Key 测通也不自动往下走
func TestWizardModelNoAdvanceIfKeyTypedDuringTest(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	d := f.deps()
	var s *S
	d.Test = func(context.Context) (bool, string) {
		s.key = "sk-another" // 测试还没回来，玩家又粘了一个 Key
		return true, "模型回答：好"
	}
	s = New(d)
	tt := ui.NewTester(func(c *ui.Context) { s.Gate(c, h) }, 1280, 900)
	tt.Frame()
	s.step, s.key = 3, "sk-test-not-real"
	tt.Frame()
	_ = plugintest.ClickButton(tt, "下一步")
	tt.Frame()
	if s.step != 3 {
		t.Errorf("测试期间粘了新 Key 就不该自动前进，现在第 %d 步", s.step)
	}
}

// 运行环境那块写现在用的文字识别后端（doctor 的 ocr 项）；SIMD 没装好时用红字说明并给出再装一次的办法。
func TestPanelShowsTheOcrBackendFromDoctor(t *testing.T) {
	cases := []struct {
		name, item string
		want       []string
	}{
		{"SIMD", `{"id":"ocr","ok":true,"detail":"SIMD PaddleOCR（PP-OCRv6，有显卡走 Vulkan）"}`, []string{"文字识别：SIMD PaddleOCR"}},
		{"fallback", `{"id":"ocr","ok":false,"detail":"RapidOCR（SIMD 没装好：找不到 .NET 10 Runtime）","next":"设置 → 运行环境 →「重装运行环境」会再试一次装 SIMD"}`,
			[]string{"文字识别：RapidOCR（SIMD 没装好", "重装运行环境"}},
	}
	for _, tc := range cases {
		f := &fake{secrets: map[string]string{}, ready: true}
		f.s = Settings{Provider: "deepseek", WizardDone: true}
		h := plugintest.New(map[string]string{"doctor": `{"items":[]}`, "doctor --probe": `{"items":[` + tc.item + `]}`})
		s := New(f.deps())
		p := s.Plugin()
		tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 1600)
		tt.Frame()
		s.Probe(h)
		tt.Frame()
		tt.Frame()
		for _, w := range tc.want {
			if !tt.HasText(w) {
				t.Errorf("%s：缺 %q：%q", tc.name, w, tt.Texts())
			}
		}
	}
}

func TestPanelHasNoOcrLineBeforeDoctorHasRun(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s = Settings{Provider: "deepseek", WizardDone: true}
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`})
	s := New(f.deps())
	p := s.Plugin()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 1600)
	tt.Frame()
	tt.Frame()
	if tt.HasText("文字识别：") {
		t.Errorf("还没读到 doctor 时不该画：%q", tt.Texts())
	}
}

// CodeRabbit #115：重装运行环境（装好 SIMD）之后，「文字识别」那一行要跟着刷新，不能一直显示旧的「没装好」；
// 游戏已经试连过（deviceOK 有值）时也要刷，而且不为此重新连游戏。
func TestReinstallRefreshesTheOcrLine(t *testing.T) {
	f := &fake{secrets: map[string]string{}, ready: true}
	f.s = Settings{Provider: "deepseek", WizardDone: true}
	stale := `{"id":"ocr","ok":false,"detail":"RapidOCR（SIMD 没装好：找不到 .NET 10 Runtime）","next":"设置 → 运行环境 →「重装运行环境」会再试一次装 SIMD"}`
	h := plugintest.New(map[string]string{"doctor": `{"items":[]}`, "doctor --probe": `{"items":[` + stale + `]}`})
	d := f.deps()
	d.BootReset = func() error { return nil }
	d.BootRun = func(context.Context, func(string)) error {
		h.Resp["doctor"] = hiroapi.Result{OK: true, Data: []byte(`{"items":[{"id":"platform","ok":null,"detail":"还没试连游戏"},{"id":"ocr","ok":true,"detail":"SIMD PaddleOCR（PP-OCRv6，有显卡走 Vulkan）"}]}`)}
		return nil
	}
	s := New(d)
	p := s.Plugin()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 1600)
	tt.Frame()
	s.Probe(h)
	tt.Frame()
	tt.Frame()
	if !tt.HasText("文字识别：RapidOCR（SIMD 没装好") {
		t.Fatalf("前提：先显示没装好：%q", tt.Texts())
	}
	probes := strings.Count(strings.Join(flat(h.Calls), "|"), "doctor --probe")
	if err := plugintest.ClickButton(tt, "重装运行环境"); err != nil {
		t.Fatalf("%v %q", err, tt.Texts())
	}
	tt.Frame()
	tt.Frame()
	tt.Frame()
	if !tt.HasText("文字识别：SIMD PaddleOCR") || tt.HasText("SIMD 没装好") {
		t.Errorf("装好后应显示 SIMD：%q", tt.Texts())
	}
	if n := strings.Count(strings.Join(flat(h.Calls), "|"), "doctor --probe"); n != probes {
		t.Errorf("刷新文字识别不该重新连游戏：试连 %d → %d 次", probes, n)
	}
}
