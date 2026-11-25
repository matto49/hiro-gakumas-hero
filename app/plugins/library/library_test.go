package library

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
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

// 11-22：「资料库把资源站的信息用上：先加上中文信息，然后再加上图标、悬浮说明」
func TestLibraryRowsUseChineseNameIconAndTip(t *testing.T) {
	common.Shared.Reset() // 别的测试查过同一编号（当时资料库是空的）
	t.Cleanup(common.Shared.Reset)
	h := plugintest.New(map[string]string{
		"catalog lookup": `{"items":{"s_card-2-0001":{"kind":"supports","name":"一起走吧！","rarity":"SSR","text":"课程结束时 Vo 上升 +10"},` +
			`"p_card-01-act-2_001":{"kind":"memories","name":"成长痛","asset":"card-p_card-01-act-2_001","text":"好调 +2 回合"}}}`,
		"catalog sprite": `{"path":"/tmp/none.webp"}`,
	})
	v := New().Cards["info"]
	for _, tc := range []struct {
		card          plugin.Card
		name, tip, ja string
	}{
		{plugin.Card{"type": "info", "kind": "supports", "items": []any{map[string]any{"id": "s_card-2-0001", "name": "一緒に行こう！", "rarity": "SSR", "attr": "Vo", "level": 50.0, "breaks": 4.0}}}, "一起走吧！", "课程结束时 Vo 上升 +10", "一緒に行こう！"},
		{plugin.Card{"type": "info", "kind": "memories", "items": []any{map[string]any{"card_id": "p_card-01-act-2_001", "card": "成長痛", "timing": "mid", "stats": map[string]any{"Vo": 100.0, "Da": 200.0, "Vi": 300.0}}}}, "成长痛", "好调 +2 回合", "成長痛"},
	} {
		tt := ui.NewTester(func(c *ui.Context) { v(c, h, tc.card) }, 760, 400)
		tt.Frame()
		tt.Frame()
		r, ok := tt.Find(tc.name)
		if !ok {
			t.Errorf("%v 应显示中文名 %q：%q", tc.card["kind"], tc.name, tt.Texts())
			continue
		}
		tt.Move(r.X+r.W/2, r.Y+r.H/2)
		tt.Advance(2e9)
		tt.Frame()
		if !tt.HasText(tc.tip) {
			t.Errorf("%v 悬停应显示效果：%q", tc.card["kind"], tt.Texts())
		}
		// CodeRabbit #92：中文名后面跟日文原名（行里和悬停说明都有）
		if !tt.HasText(tc.ja) {
			t.Errorf("%v 应保留日文原名 %q：%q", tc.card["kind"], tc.ja, tt.Texts())
		}
	}
}
