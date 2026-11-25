package shell

import (
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 11-25 review：「这个大卡片整体都是列下去有点太长了」「租借在卡的最后打一个 tag，前面还是要图标」「hover 展示详细信息」
func TestExplainGroupTilesSideBySideTagAtEndAndTip(t *testing.T) {
	g := plugin.ExplainGroup{Title: "支援卡"}
	for _, n := range []string{"甲", "乙", "丙", "丁", "戊"} {
		g.Items = append(g.Items, plugin.ExplainItem{Name: "支援卡" + n, Note: "Lv60", Tip: "支援卡" + n + " 的效果"})
	}
	g.Items = append(g.Items, plugin.ExplainItem{Name: "租来的卡", Tag: "租借", Tip: "租借：开局时借"})
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Width(560).Children(func() { explainGroup(c, "g", g) })
	}, 800, 600)
	tt.Frame()
	a, _ := tt.Find("支援卡甲")
	b, _ := tt.Find("支援卡乙")
	if a.Y != b.Y {
		t.Errorf("两格应该排在同一行（不再一张一行）：%v %v", a, b)
	}
	last, _ := tt.Find("支援卡戊")
	if last.Y-a.Y > 3*a.H+80 {
		t.Errorf("6 张卡太高了：首行 y=%v 末格 y=%v", a.Y, last.Y)
	}
	name, ok1 := tt.Find("租来的卡")
	tag, ok2 := tt.Find("租借")
	if !ok1 || !ok2 || tag.X <= name.X {
		t.Errorf("租借 tag 要在名字后面：名字 %v tag %v", name, tag)
	}
	tt.Move(a.X+a.W/2, a.Y+a.H/2)
	tt.Advance(2e9)
	tt.Frame()
	if !tt.HasText("支援卡甲 的效果") {
		t.Errorf("鼠标停上去要看到说明：%q", tt.Texts())
	}
}
