package usage

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

const jobJSON = `{"job":"J1","calls":12,"input":20000,"output":4000,"cache_read":10000,"cache_write":0,"total":34000,
 "cost":{"CNY":0.42},"subscription_usd":0,"unpriced":0,"no_usage":0,"failed":0,
 "by_source":{"decision":{"calls":10,"total":30000,"cost":{"CNY":0.40},"subscription_usd":0,"unpriced":0,"no_usage":0},
              "rescue":{"calls":2,"total":4000,"cost":{"CNY":0.02},"subscription_usd":0,"unpriced":0,"no_usage":0}},
 "by_model":[{"provider":"deepseek","model":"deepseek-flash","calls":12,"total":34000,"cost":{"CNY":0.42}}],
 "items":[{"ts":"2026-11-25T07:03:33.000+08:00","source":"decision","provider":"deepseek","model":"deepseek-flash","tokens":3000,"cost":0.03,"currency":"CNY","kind":"priced","ok":true}],
 "prices_checked":"2026-11-25"}`

const dailyJSON = `{"rows":[{"key":"2026-11-24","calls":40,"total":500000,"cost":{"CNY":0.40},"subscription_usd":0,"unpriced":0,"no_usage":0}],
 "totals":{"calls":40,"total":500000,"cost":{"CNY":0.40},"subscription_usd":0,"unpriced":0,"no_usage":0},"prices_checked":"2026-11-25"}`

const monthlyJSON = `{"rows":[{"key":"2026-11","calls":300,"total":4000000,"cost":{"CNY":3.2},"subscription_usd":0.5,"unpriced":0,"no_usage":0,
 "models":[{"provider":"deepseek","model":"deepseek-flash","calls":290,"total":3900000,"cost":{"CNY":3.2}},
           {"provider":"claude-cli","model":"claude-sonnet-5-5","calls":10,"total":100000,"cost":{},"subscription_usd":0.5}]}],
 "totals":{"calls":300,"total":4000000,"cost":{"CNY":3.2},"subscription_usd":0.5},"prices_checked":"2026-11-25"}`

const recentJSON = `{"jobs":[
 {"job":"20261124-010000-aaaa","kind":"hajime","summary":"初 · 篠澤広","calls":12,"total":34000,"cost":{"CNY":0.42}},
 {"job":"20261123-010000-bbbb","kind":"hajime","summary":"初 · 月村手毬","calls":8,"total":20000,"cost":{"CNY":0.30}}],
 "prices_checked":"2026-11-25"}`

func host() *plugintest.Host {
	return plugintest.New(map[string]string{
		"usage job":     jobJSON,
		"usage daily":   dailyJSON,
		"usage monthly": monthlyJSON,
		"usage recent":  recentJSON,
	})
}

func g(js string) Group {
	var x Group
	_ = decode([]byte(js), &x)
	return x
}

func TestCostLineFormatting(t *testing.T) {
	cases := []struct{ in, want string }{
		{`{"calls":12,"total":34000,"cost":{"CNY":0.42}}`, "12 次 · 3.4 万 token · 约 ¥0.42"},
		{`{"calls":3,"total":800,"cost":{"USD":0.0123}}`, "3 次 · 800 token · 约 $0.01"},
		{`{"calls":1,"total":900,"cost":{"CNY":0.0042}}`, "1 次 · 900 token · 约 ¥0.0042"},
		{`{"calls":4,"total":18807,"cost":{"CNY":0.009911}}`, "4 次 · 1.9 万 token · 约 ¥0.0099"},
		{`{"calls":1,"total":10,"cost":{"CNY":0.00001234}}`, "1 次 · 10 token · 约 ¥0.000012"},
		{`{"calls":2,"total":5000,"cost":{},"subscription_usd":0.05}`, "2 次 · 5,000 token · 订阅（不另收费，折合 API 约 $0.05）"},
		{`{"calls":2,"total":5000,"cost":{},"unpriced":2}`, "2 次 · 5,000 token · 花费 —"},
		{`{"calls":4,"total":5000,"cost":{"CNY":0.1},"unpriced":1}`, "4 次 · 5,000 token · 约 ¥0.10（1 次未定价）"},
		{`{"calls":1,"total":0,"cost":{},"no_usage":1}`, "1 次 · 用量不详"},
		{`{"calls":0,"total":0,"cost":{}}`, "没有调用模型"},
		{`{"calls":5,"total":120000000,"cost":{"CNY":12.5,"USD":1}}`, "5 次 · 1.2 亿 token · 约 ¥12.50 + $1.00"},
	}
	for _, c := range cases {
		if got := Line(g(c.in)); got != c.want {
			t.Errorf("%s → %q want %q", c.in, got, c.want)
		}
	}
}

func TestOnJobEndPostsUsageCard(t *testing.T) {
	h, p := host(), New()
	p.OnJobEnd(h, "J1", &plugin.JobView{State: "done", Job: &plugin.Job{ID: "J1", Kind: "hajime"}})
	if len(h.Posted) != 1 || h.Posted[0]["type"] != "usage-job" || h.Posted[0]["jobId"] != "J1" || h.Posted[0]["usage"] == nil {
		t.Fatalf("posted %+v", h.Posted)
	}
	if !h.Called("usage job J1") {
		t.Fatalf("calls %v", h.Calls)
	}
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() { p.Cards["usage-job"](c, h, h.Posted[0]) })
	}, 760, 400)
	for _, s := range []string{"本局花费", "12 次 · 3.4 万 token · 约 ¥0.42", "决策 10 次", "兜底 2 次"} {
		if !tt.HasText(s) {
			t.Errorf("卡上没有 %q：%q", s, tt.Texts())
		}
	}
	if err := plugintest.ClickButton(tt, "看明细"); err != nil || h.Panel != [2]string{"usage", "job/J1"} {
		t.Fatalf("看明细 %v %v", err, h.Panel)
	}
}

func TestOnJobEndSkipsNonPlayJobWithoutCalls(t *testing.T) {
	h := plugintest.New(map[string]string{"usage job": `{"job":"J2","calls":0,"total":0,"cost":{}}`})
	New().OnJobEnd(h, "J2", &plugin.JobView{State: "done", Job: &plugin.Job{ID: "J2", Kind: "daily"}})
	if len(h.Posted) != 0 {
		t.Fatalf("领日常这类不用模型的任务不发卡：%+v", h.Posted)
	}
	New().OnJobEnd(h, "J2", &plugin.JobView{State: "done", Job: &plugin.Job{ID: "J2", Kind: "hajime"}})
	if len(h.Posted) != 1 {
		t.Fatal("打一局没调模型也要说一声")
	}
}

func TestFooterUsesCacheNotCall(t *testing.T) {
	h, p := host(), New()
	draw := func() *ui.Tester {
		return ui.NewTester(func(c *ui.Context) { ui.Column(c).Fill().Children(func() { p.Footer(c, h) }) }, 232, 60)
	}
	draw()
	n := len(h.Calls)
	if n == 0 {
		t.Fatal("第一次要在后台取一次")
	}
	tt := draw()
	draw()
	if len(h.Calls) != n {
		t.Fatalf("15 秒内不该再调：%v", h.Calls)
	}
	if !tt.HasText("今天 约 ¥0.40") {
		t.Errorf("footer %q", tt.Texts())
	}
	h.At = h.At.Add(16 * time.Second)
	h.Live = "J1"
	draw()
	tt = draw()
	if len(h.Calls) == n || !h.Called("usage job J1") {
		t.Fatalf("过了 15 秒、有活任务：要取本局 %v", h.Calls)
	}
	if !tt.HasText("本局 约 ¥0.42 · 12 次") {
		t.Errorf("footer %q", tt.Texts())
	}
}

func TestPanelRendersHome(t *testing.T) {
	h, p := host(), New()
	h.At = time.Date(2026, 11, 24, 9, 0, 0, 0, time.Local)
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 1200)
	for _, s := range []string{"今天", "40 次 · 50 万 token · 约 ¥0.40", "本月", "deepseek-flash", "claude-sonnet-5-5",
		"最近每局", "初 · 篠澤広", "初 平均每局 约 ¥0.36（2 局）", "花费按 2026-11-25 价格表估算，以服务商账单为准"} {
		if !tt.HasText(s) {
			t.Errorf("面板没有 %q：%q", s, tt.Texts())
		}
	}
	if !h.Called("usage daily --since 2026-11-24") || !h.Called("usage monthly --since 2026-11-01 --breakdown") {
		t.Errorf("calls %v", h.Calls)
	}
}

func TestPanelJobRoute(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "job/J1") }), 520, 900)
	for _, s := range []string{"12 次 · 3.4 万 token · 约 ¥0.42", "决策", "兜底", "deepseek-flash", "07:03"} {
		if !tt.HasText(s) {
			t.Errorf("一局明细没有 %q：%q", s, tt.Texts())
		}
	}
}

func TestToolCardRendersTable(t *testing.T) {
	h, p := host(), New()
	var data map[string]any
	_ = decode([]byte(dailyJSON), &data)
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Column(c).Fill().Children(func() {
			p.Cards["usage-table"](c, h, plugin.Card{"type": "usage-table", "tool": "usage daily", "data": data})
		})
	}, 760, 400)
	if !tt.HasText("2026-11-24") || !tt.HasText("40 次 · 50 万 token · 约 ¥0.40") {
		t.Errorf("表格卡 %q", tt.Texts())
	}
	for _, name := range []string{"usage daily", "usage monthly", "usage job", "usage chat", "usage recent"} {
		found := false
		for _, n := range p.Tools {
			found = found || n == name
		}
		if !found {
			t.Errorf("没认领 %s", name)
		}
	}
}

func TestContextSummary(t *testing.T) {
	h, p := host(), New()
	out := p.Context(context.Background(), h)
	if !strings.Contains(out, "今天是 2026-11-24") { // 模型不知道年份，查用量时 --since 要写全
		t.Errorf("context 没写完整日期 %q", out)
	}
	if !strings.Contains(out, "今天模型花费 约 ¥0.40") || !strings.Contains(out, "本月 约 ¥3.20") {
		t.Errorf("context %q", out)
	}
	if gr := p.Greeting(h); gr != "" && !strings.Contains(gr, "本月") {
		t.Errorf("greeting %q", gr)
	}
}

func TestOpenCommand(t *testing.T) {
	p := New()
	if len(p.Commands) != 1 || p.Commands[0].Name != "用量" || p.ID != "usage" || p.Panel == nil {
		t.Fatalf("%+v", p.Commands)
	}
}

func TestJobPageTitleUsesRecentSummary(t *testing.T) {
	h, p := host(), New()
	_ = ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 900)
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "job/20261124-010000-aaaa") }), 520, 900)
	if !tt.HasText("11-24 01:00 初 · 篠澤広") || tt.HasText("20261124-010000-aaaa") {
		t.Errorf("标题 %q", tt.Texts())
	}
}

func TestFooterSkipsRefreshWhenLedgerUnchanged(t *testing.T) {
	h, p := host(), New()
	h.HomeDir = t.TempDir()
	ledger := filepath.Join(h.HomeDir, "usage", h.At.Format("2006-01")+".jsonl")
	_ = os.MkdirAll(filepath.Dir(ledger), 0o755)
	_ = os.WriteFile(ledger, []byte("{}\n"), 0o644)
	draw := func() {
		ui.NewTester(func(c *ui.Context) { ui.Column(c).Fill().Children(func() { p.Footer(c, h) }) }, 232, 60)
	}
	draw()
	n := len(h.Calls)
	h.At = h.At.Add(time.Minute)
	draw()
	if len(h.Calls) != n {
		t.Fatalf("流水账没变就不该再起进程：%v", h.Calls[n:])
	}
	_ = os.WriteFile(ledger, []byte("{}\n{}\n"), 0o644) // 多了一行
	h.At = h.At.Add(time.Minute)
	draw()
	if len(h.Calls) == n {
		t.Fatal("流水账变了要刷新")
	}
	n = len(h.Calls)
	h.At = h.At.Add(time.Minute)
	draw()
	if len(h.Calls) != n {
		t.Fatal("刷新过一次后，没再变就不再刷新")
	}
}

func TestPanelSkipsReloadWhenLedgerUnchanged(t *testing.T) {
	h, p := host(), New()
	h.HomeDir = t.TempDir()
	panel := func() { ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 520, 900) }
	panel()
	n := len(h.Calls)
	h.At = h.At.Add(time.Minute)
	panel()
	if len(h.Calls) != n {
		t.Fatalf("面板开着、流水账没变就不重读：%v", h.Calls[n:])
	}
}

func TestJobPageNoReloadForFinishedJob(t *testing.T) {
	h, p := host(), New()
	page := func() { ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "job/J1") }), 520, 900) }
	page()
	n := len(h.Calls)
	h.At = h.At.Add(time.Minute)
	page()
	if len(h.Calls) != n {
		t.Fatalf("打完的局用量不会变，不重读：%v", h.Calls[n:])
	}
}
