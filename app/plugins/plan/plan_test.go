package plan

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

const planJSON = `{"created":"2026-11-23 20:00","idol":{"id":"i_card-skin-ttmr-3-0012","name":"月村手毬"},"main_attr":"Vo",
 "support":{"set":2,"own":[{"id":"s_card-2-0001","name":"一緒に行こう！","attr":"Vo","level":50,"why":["Vo 主力"]}],"rental":{"id":"s_card-3-0070","name":"光に向かって","attr":"Vo"}},
 "memory":{"set":1,"chosen":[{"card_id":"p_card-1","name":"アイドル宣言","upgrade":1,"timing":"start"}]},"notes":["主属性按初定 Vo"]}`

func host() *plugintest.Host {
	return plugintest.New(map[string]string{
		"plan show":      planJSON,
		"catalog lookup": `{"items":{"s_card-2-0001":{"name":"一起去吧！"}}}`,
		"catalog sprite": `{"path":""}`,
	})
}

func TestFormationCardShowsSupportsAndMemories(t *testing.T) {
	h, p := host(), New()
	var card map[string]any
	_ = decode(planJSON, &card)
	v := p.Cards["formation"]
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { v(c, h, plugin.Card{"type": "formation", "plan": card}) })
	}, 760, 600)
	for _, s := range []string{"开局编成方案", "月村手毬", "一起去吧！", "租借", "アイドル宣言", "开局"} {
		if !tt.HasText(s) {
			t.Errorf("编成卡没有 %q：%q", s, tt.Texts())
		}
	}
}

func TestWriteFormationAsksConfirm(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 900)
	if err := plugintest.ClickButton(tt, "写进游戏"); err != nil {
		t.Fatalf("%v %q", err, tt.Texts())
	}
	if len(h.RunArgs_) != 1 || strings.Join(h.RunArgs_[0][1:], " ") != "run start write-formation --scenario hajime" {
		t.Errorf("写编成：%v", h.RunArgs_)
	}
}

func TestContextSummarizesPlan(t *testing.T) {
	h, p := host(), New()
	out := p.Context(context.Background(), h)
	for _, s := range []string{"月村手毬", "主属性 声乐", "支援卡编成 2", "回忆编成 1", "11-23"} {
		if !strings.Contains(out, s) {
			t.Errorf("上下文没有 %q：\n%s", s, out)
		}
	}
}

// CR#11：对话里出了新的编成卡后，面板要重新读，不能还显示旧方案。
func TestPanelReloadsAfterNewFormationCard(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 900)
	h.Resp["plan show"] = okRes(strings.Replace(planJSON, "2026-11-23 20:00", "2026-11-24 09:00", 1))
	var card map[string]any
	_ = decode(strings.Replace(planJSON, "2026-11-23 20:00", "2026-11-24 09:00", 1), &card)
	cv := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Children(func() { p.Cards["formation"](c, h, plugin.Card{"type": "formation", "plan": card}) })
	}, 600, 600)
	_ = cv
	tt.Frame()
	if !tt.HasText("出方案时间 2026-11-24 09:00") {
		t.Errorf("面板没换成新方案：%q", tt.Texts())
	}
}

func okRes(s string) hiroapi.Result { return hiroapi.Result{OK: true, Data: []byte(s)} }

// CR2#4：对话里旧的编成卡一直在画，也不能把面板里新的方案清掉。
func TestOldFormationCardDoesNotClearNewerPlan(t *testing.T) {
	h, p := host(), New()
	newer := strings.Replace(planJSON, "2026-11-23 20:00", "2026-11-24 09:00", 1)
	h.Resp["plan show"] = okRes(newer)
	var old map[string]any
	_ = decode(planJSON, &old)
	n := 0
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() {
			p.Cards["formation"](c, h, plugin.Card{"type": "formation", "plan": old})
			p.Panel(c, h, "")
		})
	}, 600, 1400)
	for i := 0; i < 3; i++ {
		tt.Frame()
	}
	for _, c := range h.Calls {
		if strings.Join(c, " ") == "plan show" {
			n++
		}
	}
	if n > 1 || !tt.HasText("出方案时间 2026-11-24 09:00") {
		t.Errorf("plan show 读了 %d 次，面板：%v", n, tt.HasText("出方案时间 2026-11-24 09:00"))
	}
}

// 11-26：方案没定编成号时，对话里的方案卡要写明（原来卡上什么都不提，开局确认后才报「new 开局要编成号」）；方案备注也要显示
func TestCardWarnsWhenSetMissingAndShowsNotes(t *testing.T) {
	h, p := host(), New()
	noSet := strings.Replace(strings.Replace(planJSON, `"set":2`, `"set":null`, 1), `"set":1`, `"set":null`, 1)
	noSet = strings.Replace(noSet, `"notes":["主属性按初定 Vo"]`, `"notes":["支援卡技能数据缺失，建议手动挑"]`, 1)
	var card map[string]any
	_ = decode(noSet, &card)
	v := p.Cards["formation"]
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { v(c, h, plugin.Card{"type": "formation", "plan": card}) })
	}, 760, 800)
	all := strings.Join(tt.Texts(), "\n")
	for _, s := range []string{"编成号未定", "建议手动挑"} {
		if !strings.Contains(all, s) {
			t.Errorf("方案卡没有 %q：%q", s, tt.Texts())
		}
	}
}

// CodeRabbit #88：编成页「重新出方案」要能填编成号（不打字的玩家也能补上），读过编成位时列出空着的号一点就填
func TestRegeneratePlanWithSetNumbers(t *testing.T) {
	h := plugintest.New(map[string]string{
		"plan show":       strings.Replace(strings.Replace(planJSON, `"set":2`, `"set":null`, 1), `"set":1`, `"set":null`, 1),
		"catalog lookup":  `{"items":{}}`,
		"catalog sprite":  `{"path":""}`,
		"info formations": `{"kind":"formations","scanned_at":"2026-11-26 01:00:00","items":[{"mode":"hajime","kind":"support","set":3,"filled":0},{"mode":"hajime","kind":"support","set":4,"filled":5},{"mode":"hajime","kind":"memory","set":7,"filled":0}]}`,
		"plan formation":  `{"ok":true}`,
	})
	p := New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 1600)
	tt.Frame()
	tt.Frame()
	for _, l := range []string{"用支援卡编成 3", "用回忆编成 7"} {
		if err := plugintest.ClickButton(tt, l); err != nil {
			t.Fatalf("%v %q", err, tt.Texts())
		}
	}
	if tt.HasText("用支援卡编成 4") {
		t.Error("有东西的编成位不该列成空的")
	}
	if err := plugintest.ClickButton(tt, "重新出方案"); err != nil {
		t.Fatal(err)
	}
	var got string
	for _, c := range h.Calls {
		if strings.HasPrefix(strings.Join(c, " "), "plan formation") {
			got = strings.Join(c, " ")
		}
	}
	if !strings.Contains(got, "--support-set 3") || !strings.Contains(got, "--memory-set 7") {
		t.Errorf("重新出方案没带编成号：%q", got)
	}
}
