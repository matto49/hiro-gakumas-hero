package library

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

func host() *plugintest.Host {
	return plugintest.New(map[string]string{
		"catalog lookup": `{"items":{}}`,
		"catalog sprite": `{"path":""}`,
		"info summary":   `{"account":{"plv":52,"ap":120,"ap_max":150},"kinds":{"supports":{"count":88,"scanned_at":"2026-11-20 21:10:00"},"memories":{"count":40,"scanned_at":"2026-11-18 20:00:00"},"idols":{"count":30,"scanned_at":null}}}`,
		"info supports":  `{"kind":"supports","scanned_at":"2026-11-20 21:10:00","items":[{"id":"s_card-2-0001","name":"一緒に行こう！","rarity":"SSR","attr":"Vo","level":50,"breaks":4}]}`,
	})
}

func cardView(t *testing.T, card plugin.Card) *ui.Tester {
	h, p := host(), New()
	v := p.Cards["info"]
	if v == nil {
		t.Fatal("资料库没认领 info 卡")
	}
	return ui.NewTester(func(c *ui.Context) { v(c, h, card) }, 760, 400)
}

func TestIdolInfoCardUsesIdolTemplate(t *testing.T) {
	tt := cardView(t, plugin.Card{"type": "info", "title": "偶像卡", "kind": "idols", "scannedAt": "2026-11-22 10:00:00",
		"items": []any{map[string]any{"id": "i_card-skin-ttmr-3-0012", "name": "【Luna say maybe】月村手毬", "title": "Luna say maybe", "idol": "月村手毬", "rarity": "SSR", "plan": "sense"}}})
	for _, s := range []string{"月村手毬", "Luna say maybe", "SSR", "偶像卡"} {
		if !tt.HasText(s) {
			t.Errorf("偶像卡没有 %q：%q", s, tt.Texts())
		}
	}
	for _, txt := range tt.Texts() {
		if strings.Contains(txt, "突破") || strings.Contains(txt, "Lv?") || txt == "?" {
			t.Errorf("偶像卡套了支援卡模板：%q", txt)
		}
	}
}

func TestSupportInfoCard(t *testing.T) {
	tt := cardView(t, plugin.Card{"type": "info", "kind": "supports", "items": []any{map[string]any{"id": "s_card-2-0001", "name": "一緒に行こう！", "rarity": "SSR", "attr": "Vo", "level": 50.0, "breaks": 4.0}}})
	if !tt.HasText("一緒に行こう！") || !tt.HasText("SSR Lv50 · 突破 4") {
		t.Errorf("支援卡：%q", tt.Texts())
	}
}

func TestContextHasAccountAndScanDates(t *testing.T) {
	h, p := host(), New()
	out := p.Context(context.Background(), h)
	for _, s := range []string{"PLv 52", "AP 120/150", "支援卡 88 张（11-20 扫过）", "回忆 40 张（11-18 扫过）", "偶像卡没扫描过"} {
		if !strings.Contains(out, s) {
			t.Errorf("上下文没有 %q：\n%s", s, out)
		}
	}
}

func TestScanPanelAsksForConfirm(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "scan") }), 520, 700)
	if err := plugintest.ClickButton(tt, "扫描支援卡"); err != nil {
		t.Fatal(err)
	}
	if len(h.RunArgs_) != 1 || strings.Join(h.RunArgs_[0][1:], " ") != "run start scan-supports" {
		t.Errorf("扫描：%v", h.RunArgs_)
	}
}

func TestGreetingAndStarter(t *testing.T) {
	h, p := host(), New()
	p.Greeting(h)
	if g := p.Greeting(h); g != "PLv 52 · AP 120/150" {
		t.Errorf("问候小字 %q", g)
	}
	st := p.Starters(h)
	if len(st) != 1 || st[0].Say == "" {
		t.Errorf("资料库引导卡 %+v", st)
	}
}

func TestChoiceCardPicksIdol(t *testing.T) {
	h, p := host(), New()
	v := p.Cards["choice"]
	if v == nil {
		t.Fatal("资料库没认领 choice 卡")
	}
	card := plugin.Card{"type": "choice", "kind": "idols", "question": "要用哪张琴音？", "status": "waiting", "options": []any{
		map[string]any{"id": "i_card-a", "name": "【世界一可愛い私】藤田ことね", "idol": "藤田ことね", "title": "世界一可愛い私", "rarity": "SSR", "plan": "logic"},
		map[string]any{"id": "i_card-b", "name": "【Campus mode!!】藤田ことね", "idol": "藤田ことね", "title": "Campus mode!!", "rarity": "SSR", "plan": "logic"},
	}}
	tt := ui.NewTester(func(c *ui.Context) { v(c, h, card) }, 760, 400)
	tt.Frame()
	for _, s := range []string{"要用哪张琴音？", "世界一可愛い私", "Campus mode!!"} {
		if !tt.HasText(s) {
			t.Errorf("选择卡没有 %q：%q", s, tt.Texts())
		}
	}
	if err := tt.Click("选【Campus mode!!】藤田ことね"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if len(h.Asked) != 1 || !strings.Contains(h.Asked[0], "Campus mode!!") || strings.Contains(h.Asked[0], "i_card") || card["status"] != "picked" || card["picked"] != "i_card-b" {
		t.Fatalf("点选后 asked=%v card=%v", h.Asked, card)
	}
	_ = tt.Click("选【世界一可愛い私】藤田ことね")
	if len(h.Asked) != 1 {
		t.Errorf("选过了不该再发：%v", h.Asked)
	}
}

// CodeRabbit #79：助手还在回复时点选会被丢掉：这时不能选，也不锁卡
func TestChoiceCardWaitsWhileBusy(t *testing.T) {
	h, p := host(), New()
	h.Busy_ = true
	card := plugin.Card{"type": "choice", "kind": "idols", "status": "waiting", "options": []any{map[string]any{"id": "i_card-a", "name": "【A】藤田ことね", "idol": "藤田ことね", "title": "A"}}}
	tt := ui.NewTester(func(c *ui.Context) { p.Cards["choice"](c, h, card) }, 760, 400)
	tt.Frame()
	_ = tt.Click("选【A】藤田ことね")
	if len(h.Asked) != 0 || card["status"] != "waiting" {
		t.Errorf("忙的时候不该选：asked=%v card=%v", h.Asked, card)
	}
}

// CodeRabbit #79：卡名还没查到（只有编号）时不让选，不给玩家看编号
func TestChoiceCardWaitsForNames(t *testing.T) {
	h, p := host(), New()
	card := plugin.Card{"type": "choice", "kind": "idols", "status": "waiting", "options": []any{map[string]any{"id": "i_card-zzz"}}}
	tt := ui.NewTester(func(c *ui.Context) { p.Cards["choice"](c, h, card) }, 760, 400)
	tt.Frame()
	for _, s := range tt.Texts() {
		if strings.Contains(s, "i_card") {
			t.Errorf("露出了编号：%q", tt.Texts())
		}
	}
	for _, l := range []string{"选i_card-zzz", "选卡名还在查"} {
		_ = tt.Click(l)
	}
	if len(h.Asked) != 0 || card["status"] != "waiting" {
		t.Errorf("卡名没查到不该能选：asked=%v", h.Asked)
	}
}

// 设计 5.7：扫描页顶部「一键全收集」发起 collect-all（同样走确认卡）
func TestScanPanelCollectAll(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "scan") }), 520, 700)
	if err := plugintest.ClickButton(tt, "一键全收集"); err != nil {
		t.Fatal(err)
	}
	if len(h.RunArgs_) != 1 || strings.Join(h.RunArgs_[0][1:], " ") != "run start collect-all" {
		t.Errorf("一键全收集：%v", h.RunArgs_)
	}
}

// 设计 5.8：回忆信息卡带片覆盖（12 片里全读过几片、最旧哪天、有没有待补）
func TestMemoryInfoCardShowsSliceCoverage(t *testing.T) {
	tt := cardView(t, plugin.Card{"type": "info", "kind": "memories", "items": []any{map[string]any{"card": "魅惑の視線", "timing": "mid"}},
		"slices": map[string]any{"done": 3.0, "total": 12.0, "oldest": "2026-11-26 04:00:00", "pending": []any{"logic:hif"}}})
	if !tt.HasText("按片：12 片里全读过 3 片 · 最旧 11-26 · 待补 1 片") {
		t.Errorf("回忆片覆盖：%q", tt.Texts())
	}
}

func TestScanPanelMemoryButtonIsIncremental(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "scan") }), 520, 800)
	if err := plugintest.ClickButton(tt, "补扫新回忆"); err != nil {
		t.Fatal(err)
	}
	if len(h.RunArgs_) != 1 || strings.Join(h.RunArgs_[0][1:], " ") != "run start scan-memories" {
		t.Errorf("补扫新回忆：%v", h.RunArgs_)
	}
}

// 设计 5.7 / D7：对话里「/全收集」发起一键全收集（同样走确认卡）
func TestCollectAllSlashCommand(t *testing.T) {
	h, p := host(), New()
	for _, cmd := range p.Commands {
		if cmd.Name == "全收集" && cmd.Run != nil {
			cmd.Run(h)
			if len(h.RunArgs_) != 1 || strings.Join(h.RunArgs_[0][1:], " ") != "run start collect-all" {
				t.Errorf("/全收集：%v", h.RunArgs_)
			}
			return
		}
	}
	t.Fatal("没有 /全收集 命令")
}
