package start

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

// planSnap 是待确认里固定的那份编成方案摘要（CLI 出待确认时存的 plan 字段）
func planSnap(t *testing.T, raw string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

const planJSON = `{"idol":{"name":"月村手毬"},"support":{"set":2,"own":[{"id":"s_card-2-0001","name":"A","level":60},{"id":"s_card-3-0012","name":"B","level":55},{"id":"s_card-3-0013","name":"C","level":50},{"id":"s_card-3-0014","name":"D","level":50},{"id":"s_card-3-0015","name":"E","level":45}],"rental":{"id":"s_card-3-0000","name":"租来的","estimated":true}},"memory":{"set":1,"chosen":[{"card_id":"p_card-01-act-2_001","name":"成長痛","timing":"mid","upgrade":0},{"name":"仕切り直し","timing":"mid","upgrade":1},{"name":"輝くキミへ","timing":"mid","upgrade":1},{"name":"アイドル宣言","timing":"mid","upgrade":0}]},"notes":[]}`

func host() *plugintest.Host {
	return plugintest.New(map[string]string{
		"catalog lookup": `{"items":{"i_card-skin-ttmr-3-0012":{"kind":"idols","name":"【Luna say maybe】月村手毬","rarity":"SSR","asset":"idol-i_card-ttmr-3-0012","attr":"Vo"},` +
			`"s_card-2-0001":{"kind":"supports","name":"支援卡甲","rarity":"SR","text":"课程结束时 Vo 上升 +10"},` +
			`"s_card-3-0000":{"kind":"supports","name":"租借乙","rarity":"SSR","text":"体力回复 +5"},` +
			`"p_card-01-act-2_001":{"kind":"memories","name":"成长痛","rarity":"SR","asset":"card-p_card-01-act-2_001","text":"好调 +2 回合"}}}`,
		"catalog sprite": `{"path":"/tmp/none.webp"}`,
		"info idols":     `{"items":[{"id":"i_card-skin-ttmr-3-0012","name":"【Luna say maybe】月村手毬","rarity":"SSR"},{"id":"i_card-skin-hski-3-0001","name":"篠澤広","rarity":"SSR"}]}`,
		"run status": `{"current":null,"recent":[
			{"id":"j1","kind":"hajime","state":"done","summary":"初","started_at":1763900000,"params":{"idol":"i_card-skin-ttmr-3-0012"}},
			{"id":"j2","kind":"hajime","state":"done","summary":"初","started_at":1763910000,"params":{"idol":"i_card-skin-ttmr-3-0012"}},
			{"id":"j3","kind":"nia","state":"failed","summary":"N.I.A","started_at":1763920000,"params":{"idol":"i_card-skin-hski-3-0001"}}]}`,
	})
}

func TestExplainHajime(t *testing.T) {
	h := host()
	p := New()
	// 第一次问：资料库在后台查（假宿主同步完成），第二次就有名字
	pd := hiroapi.Pending{Command: "hiro run start hajime --idol i_card-skin-ttmr-3-0012", Summary: "初", Plan: planSnap(t, planJSON)}
	p.Explain(h, pd)
	e, ok := p.Explain(h, pd)
	if !ok {
		t.Fatal("认不出 run start")
	}
	if e.Title != "开一局 初" || !strings.Contains(e.Subtitle, "月村手毬") || !strings.Contains(e.Subtitle, "REGULAR") || e.Attr != "Vo" {
		t.Errorf("解读 %+v", e)
	}
	if e.Art != "/tmp/none.webp" {
		t.Errorf("卡面 %q", e.Art)
	}
	if len(e.Rows) == 0 || !strings.Contains(e.Rows[0][1], "沿用编成方案") {
		t.Errorf("编成行 %+v", e.Rows)
	}
	// 11-25 review：编成要列全，支援卡 6 张（5 张自有 + 租借）、回忆 4 份
	if len(e.Groups) != 2 || e.Groups[0].Title != "支援卡" || len(e.Groups[0].Items) != 6 || e.Groups[1].Title != "回忆" || len(e.Groups[1].Items) != 4 {
		t.Fatalf("编成分组 %+v", e.Groups)
	}
	if it := e.Groups[0].Items[5]; !strings.Contains(it.Name, "租借乙") || it.Tag != "租借" {
		t.Errorf("租借格 %+v", it)
	}
	if it := e.Groups[1].Items[1]; it.Name != "仕切り直し+" || it.Tag != "中间" {
		t.Errorf("回忆 %+v", it)
	}
	if !strings.Contains(e.Cost, "会操作游戏") {
		t.Errorf("代价 %q", e.Cost)
	}
}

// 待确认里 command 只有「run start」（CLI 存的是命令名），种类和参数在 args 里（11-25 review：所有确认卡都退回兜底样式）
func TestExplainFromArgs(t *testing.T) {
	h, p := host(), New()
	pd := hiroapi.Pending{Command: "run start", Summary: "初", Args: map[string]any{
		"kind": "hajime", "idol": "i_card-skin-ttmr-3-0012", "difficulty": "pro", "support_set": nil, "no_llm": false, "command": nil}}
	p.Explain(h, pd)
	e, ok := p.Explain(h, pd)
	if !ok || e.Title != "开一局 初" || !strings.Contains(e.Subtitle, "月村手毬") || !strings.Contains(e.Subtitle, "PRO") {
		t.Fatalf("按 args 解读 %+v", e)
	}
	if _, ok := p.Explain(h, hiroapi.Pending{Command: "run start"}); ok {
		t.Error("没有 args 也没有种类，不该认")
	}
}

// CodeRabbit #198：刷回忆确认卡上的转写目标写卡名，不露编号；填的就是卡名的照原样。
func TestExplainFarmTargetShowsName(t *testing.T) {
	h, p := host(), New()
	pd := hiroapi.Pending{Command: "run start", Summary: "初", Args: map[string]any{
		"kind": "hajime", "command": "resume", "idol": "i_card-skin-ttmr-3-0012", "farm": "bad", "farm_target": "p_card-01-act-2_001,輝くキミへ"}}
	p.Explain(h, pd)
	e, ok := p.Explain(h, pd)
	if !ok {
		t.Fatal("没认出")
	}
	got := ""
	for _, r := range e.Rows {
		if r[0] == "转写目标" {
			got = r[1]
		}
	}
	if got != "成长痛、輝くキミへ" {
		t.Errorf("转写目标 %q（%+v）", got, e.Rows)
	}
}

// 不是培育的任务不能写「用掉 1 次培育」（11-25 review）
func TestExplainNonProduceKinds(t *testing.T) {
	h, p := host(), New()
	cases := []struct {
		args        map[string]any
		title, cost string
	}{
		{map[string]any{"kind": "write-formation", "dry_run": true}, "对照编成方案", "只看画面，不改东西"},
		{map[string]any{"kind": "write-formation", "scenario": "hajime"}, "把编成方案写进游戏", "会改游戏里的编成"},
		{map[string]any{"kind": "main-task"}, "做初星课题", "会操作游戏"},
		{map[string]any{"kind": "check-settings", "fix": true}, "把游戏设置改好", "只动快进、跳过演出那 5 个开关"},
		{map[string]any{"kind": "story", "chapter": 2}, "看初星剧情", "会操作游戏"},
		{map[string]any{"kind": "contest-unit", "dry_run": true}, "看竞赛编队总合力", "只看画面，不改东西"},
		{map[string]any{"kind": "contest-unit"}, "竞赛编队", "会改竞赛编队"},
		{map[string]any{"kind": "scan-memories"}, "扫描回忆", "只看画面，不改东西"},
		{map[string]any{"kind": "selftest", "seconds": 5}, "自检", "只看画面，不改东西"},
	}
	for _, c := range cases {
		e, ok := p.Explain(h, hiroapi.Pending{Command: "run start", Args: c.args})
		if !ok || e.Title != c.title || !strings.Contains(e.Cost, c.cost) || strings.Contains(e.Cost, "培育") {
			t.Errorf("%v → %+v", c.args, e)
		}
	}
}

func TestExplainOtherKinds(t *testing.T) {
	h, p := host(), New()
	if e, ok := p.Explain(h, hiroapi.Pending{Command: "hiro run start hif --command resume"}); !ok || e.Title != "接着打 H.I.F" {
		t.Errorf("续打 %+v", e)
	}
	if e, ok := p.Explain(h, hiroapi.Pending{Command: "hiro run start daily"}); !ok || e.Title != "领日常奖励" {
		t.Errorf("日常 %+v", e)
	}
	if _, ok := p.Explain(h, hiroapi.Pending{Command: "hiro write-formation --scenario x"}); ok {
		t.Error("不是 run start 不该认")
	}
}

func TestPanelStartsWithChosenIdol(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 760)
	if err := tt.Click("初"); err != nil {
		t.Fatal(err)
	}
	if err := tt.Click("篠澤広"); err != nil {
		t.Fatalf("偶像列表：%v %q", err, tt.Texts())
	}
	if err := tt.Click("PRO"); err != nil {
		t.Fatal(err)
	}
	if err := tt.Click("发起"); err != nil {
		t.Fatal(err)
	}
	if len(h.RunArgs_) != 1 {
		t.Fatalf("没发起：%v", h.RunArgs_)
	}
	got := strings.Join(h.RunArgs_[0], " ")
	if !strings.Contains(got, "run start hajime --command new --exam-search mid1,final,lesson --idol i_card-skin-hski-3-0001 --difficulty pro") {
		t.Errorf("参数 %q", got)
	}
}

func TestContextCountsRecentAndFrequent(t *testing.T) {
	h, p := host(), New()
	p.Context(context.Background(), h) // 让资料库名字进缓存
	out := p.Context(context.Background(), h)
	for _, s := range []string{"初", "完成", "月村手毬（2 局）", "N.I.A（1 局）"} {
		if !strings.Contains(out, s) {
			t.Errorf("上下文里没有 %q：\n%s", s, out)
		}
	}
}

func TestStartersFromRecentRuns(t *testing.T) {
	h, p := host(), New()
	p.Starters(h) // 第一次在后台读最近几局（假宿主同步）
	st := p.Starters(h)
	if len(st) < 2 || st[0].Label != "用月村手毬再打一把初" || st[0].Say != "帮我用月村手毬打一把初" || st[0].Art != "/tmp/none.webp" {
		t.Fatalf("引导卡 %+v", st)
	}
	found := false
	for _, s := range st {
		if s.Label == "领日常奖励" && s.Run != nil {
			found = true
		}
	}
	if !found {
		t.Error("要有领日常奖励")
	}
}

// CodeRabbit #197：偶像之路的参数是偶像名，资料库按编号查不到，首页照样给「再打一把」。
func TestStartersTowerByName(t *testing.T) {
	h, p := host(), New()
	h.Resp["run status"] = okResult(`{"current":null,"recent":[{"id":"j1","kind":"tower","state":"done","started_at":1764000000,"params":{"idol":"篠澤広"}}]}`)
	p.Starters(h)
	st := p.Starters(h)
	if len(st) == 0 || st[0].Label != "用篠澤広再打一把偶像之路" {
		t.Errorf("偶像之路的再打一把：%+v", st)
	}
}

// CodeRabbit #198：重放不了的记录（刷回忆的局）不占首页名额、不挡住同一偶像更早的普通局；全都重放不了时保留「选个偶像开一局」。
func TestStartersSkipUnreplayable(t *testing.T) {
	h, p := host(), New()
	h.Resp["run status"] = okResult(`{"current":null,"recent":[
		{"id":"f1","kind":"hajime","state":"done","started_at":1764000000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"resume","farm":"bad","farm_target":"輝くキミへ"}},
		{"id":"f2","kind":"hajime","state":"done","started_at":1763990000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"resume","farm":"bad","farm_target":"輝くキミへ"}},
		{"id":"j1","kind":"hajime","state":"done","started_at":1763900000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"new"}}]}`)
	p.Starters(h)
	st := p.Starters(h)
	if len(st) < 2 || st[0].Label != "用月村手毬再打一把初" || st[0].Page == nil || st[1].Label != "领日常奖励" {
		t.Fatalf("刷回忆的局不占名额，下面那局普通的照样出：%+v", st)
	}

	h, p = host(), New()
	h.Resp["run status"] = okResult(`{"current":null,"recent":[
		{"id":"f1","kind":"hajime","state":"done","started_at":1764000000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"resume","farm":"bad"}}]}`)
	p.Starters(h)
	st = p.Starters(h)
	if len(st) == 0 || st[0].Say != "/开局" {
		t.Errorf("全都重放不了时保留「选个偶像开一局」：%+v", st)
	}
}

// CodeRabbit #198：续打的记录缺难度时，「再打一把」不改成 new 按缺省开 REGULAR：不出这一项，找更早的完整记录。
func TestReplayResumeWithoutDifficulty(t *testing.T) {
	j := plugin.Job{Kind: "hajime", Params: map[string]any{"command": "resume", "idol": "i_card-skin-ttmr-3-0012"}}
	if replay(j, "手毬", "初") != nil {
		t.Error("续打记录缺难度：不能按缺省难度新开")
	}
	j.Params["difficulty"] = "master"
	if replay(j, "手毬", "初") == nil {
		t.Error("续打记录带了难度：照常再打一把")
	}
	hif := plugin.Job{Kind: "hif", Params: map[string]any{"command": "resume", "idol": "i_card-skin-ttmr-3-0012", "mode": "qualify"}}
	if replay(hif, "手毬", "H.I.F") == nil {
		t.Error("H.I.F 没有难度：续打记录照常再打一把")
	}

	h, p := host(), New()
	h.Resp["run status"] = okResult(`{"current":null,"recent":[
		{"id":"r1","kind":"hajime","state":"done","started_at":1764000000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"resume"}},
		{"id":"j1","kind":"hajime","state":"done","started_at":1763900000,"params":{"idol":"i_card-skin-ttmr-3-0012","command":"new","difficulty":"pro"}}]}`)
	p.Starters(h)
	st := p.Starters(h)
	if len(st) < 2 || st[0].Label != "用月村手毬再打一把初" || st[0].Page == nil {
		t.Fatalf("缺难度的续打不占名额，更早那局完整的照样出：%+v", st)
	}
	ph := plugintest.New(nil)
	st[0].Page(ph)
	if len(ph.RunArgs_) != 1 || !strings.Contains(strings.Join(ph.RunArgs_[0], " "), "--difficulty pro") {
		t.Errorf("按那局的难度开：%q", ph.RunArgs_)
	}
}

func TestStartersWithoutHistoryOpenPanel(t *testing.T) {
	h, p := host(), New()
	h.Resp["run status"] = h.Resp["info idols"] // 形状不对 → 当没有最近几局
	h.Resp["run status"] = okResult(`{"current":null,"recent":[]}`)
	p.Starters(h)
	st := p.Starters(h)
	if len(st) == 0 || st[0].Say != "/开局" {
		t.Errorf("没有最近几局时引导去开局面板：%+v", st)
	}
}

func okResult(s string) hiroapi.Result { return hiroapi.Result{OK: true, Data: []byte(s)} }

func TestIdolPickerShowsChineseName(t *testing.T) {
	// 11-25 Windows：整页中文，只有偶像列表是扫描读到的日文
	h, p := host(), New()
	h.Resp["catalog lookup"] = hiroapi.Result{OK: true, Data: []byte(`{"items":{"i_card-skin-kcna-3-0099":{"kind":"idols","name":"【荆棘之路】仓本千奈","rarity":"SSR","asset":"idol-i_card-kcna-3-0099"}}}`)}
	h.Resp["info idols"] = hiroapi.Result{OK: true, Data: []byte(`{"items":[{"id":"i_card-skin-kcna-3-0099","name":"【ガラクタロード】倉本千奈","rarity":"SSR"}]}`)}
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 760)
	if err := tt.Click("初"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if !tt.HasText("【荆棘之路】仓本千奈") || tt.HasText("【ガラクタロード】倉本千奈") {
		t.Fatalf("没换成中文名：%q", tt.Texts())
	}
	if err := tt.Click("【荆棘之路】仓本千奈"); err != nil {
		t.Fatal(err)
	}
	if !tt.HasText("已选：【荆棘之路】仓本千奈") {
		t.Errorf("已选不是中文名：%q", tt.Texts())
	}
}

func TestScanIdolsEndReloadsList(t *testing.T) {
	h, p := host(), New()
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Panel(c, h, "") }), 560, 760)
	if err := tt.Click("初"); err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, c := range h.Calls {
		if strings.Join(c, " ") == "info idols" {
			n++
		}
	}
	p.OnJobEnd(h, "J1", &plugin.JobView{Job: &plugin.Job{ID: "J1", Kind: "scan-idols"}, State: "done"})
	tt.Frame()
	m := 0
	for _, c := range h.Calls {
		if strings.Join(c, " ") == "info idols" {
			m++
		}
	}
	if m != n+1 {
		t.Errorf("扫完偶像卡没重读列表：%d → %d", n, m)
	}
}

// 确认卡上的编成方案只看待确认里固定的那份，不再另外去读 plan show（PR #79 CodeRabbit：缓存 1 分钟，方案改了卡上还是旧的）
func TestExplainUsesPendingPlanSnapshot(t *testing.T) {
	h, p := host(), New()
	pd := hiroapi.Pending{Command: "run start", Plan: planSnap(t, planJSON),
		Args: map[string]any{"kind": "write-formation", "scenario": "hajime"}}
	e, ok := p.Explain(h, pd)
	if !ok || len(e.Groups) != 2 || len(e.Groups[0].Items) != 6 || e.Groups[1].Items[0].Name != "成长痛" {
		t.Fatalf("写编成卡的方案分组 %+v", e.Groups)
	}
	// 方案换了（待确认里是另一份）：卡跟着待确认走，同一个 Plugin 连问两次也不串
	other := planSnap(t, `{"idol":{"name":"月村手毬"},"support":{"set":9,"own":[],"rental":null},"memory":{"set":8,"chosen":[{"name":"新回忆","timing":"mid","upgrade":0}]}}`)
	pd2 := hiroapi.Pending{Command: "run start", Plan: other, Args: map[string]any{"kind": "hajime", "idol": "i_card-skin-ttmr-3-0012"}}
	e2, _ := p.Explain(h, pd2)
	if len(e2.Groups) != 1 || e2.Groups[0].Items[0].Name != "新回忆" || !strings.Contains(e2.Rows[0][1], "支援卡 9 号、回忆 8 号") {
		t.Errorf("第二张卡 %+v %+v", e2.Rows, e2.Groups)
	}
	if h.Called("plan show") {
		t.Error("不该再调 plan show")
	}
	// 没有快照（旧 CLI 出的待确认）：不画方案，也不去读
	e3, _ := p.Explain(h, hiroapi.Pending{Command: "run start", Args: map[string]any{"kind": "write-formation"}})
	if len(e3.Groups) != 0 || h.Called("plan show") {
		t.Errorf("没快照 %+v", e3.Groups)
	}
}

// 11-25 review：「租借在卡的最后打一个 tag，前面还是要图标；回忆也要图标，hover 展示详细信息」
func TestExplainItemsHaveIconsAndTips(t *testing.T) {
	h := host()
	p := New()
	pend := hiroapi.Pending{Command: "hiro run start hajime --idol i_card-skin-ttmr-3-0012", Summary: "初", Plan: planSnap(t, planJSON)}
	p.Explain(h, pend)
	e, _ := p.Explain(h, pend)
	sup, mem := e.Groups[0].Items, e.Groups[1].Items
	if sup[0].Name != "支援卡甲" || !strings.Contains(sup[0].Tip, "课程结束时 Vo 上升 +10") || !strings.Contains(sup[0].Tip, "Lv60") {
		t.Errorf("支援卡要用中文名、悬停带效果和等级：%+v", sup[0])
	}
	if r := sup[5]; r.Thumb == "" || r.Tag != "租借" || !strings.Contains(r.Tip, "租借") {
		t.Errorf("租借格要有图标、tag 和说明：%+v", r)
	}
	if m := mem[0]; m.Thumb == "" || m.Name != "成长痛" || !strings.Contains(m.Tip, "好调 +2 回合") {
		t.Errorf("回忆要有图标、中文名和效果说明：%+v", m)
	}
}

// G1-F3（11-26 小白验收）：方案还没定编成号（开局前挑空白编成位）时，确认卡按方案画、写明开局前要做什么，
// 不能写成「沿用游戏里当前选中的编成」（和上面那句「把编成方案写进去」说反了）。
func TestExplainPlanWithoutSetWhenPreparing(t *testing.T) {
	h, p := host(), New()
	noSet := strings.Replace(strings.Replace(planJSON, `"set":2`, `"set":null`, 1), `"set":1`, `"set":null`, 1)
	pd := hiroapi.Pending{Command: "run start", Plan: planSnap(t, noSet), Args: map[string]any{
		"kind": "hajime", "idol": "i_card-skin-ttmr-3-0012", "prepare": true,
		"prepare_note": "先读一遍「初」的编成位，挑空着的把编成方案写进去"}}
	p.Explain(h, pd)
	e, ok := p.Explain(h, pd)
	if !ok {
		t.Fatal("没认出")
	}
	rows := map[string]string{}
	for _, r := range e.Rows {
		rows[r[0]] = r[1]
	}
	if !strings.Contains(rows["编成"], "按编成方案") || strings.Contains(rows["编成"], "沿用游戏里") {
		t.Errorf("编成行 %q", rows["编成"])
	}
	if !strings.Contains(rows["开局前"], "挑空着的") {
		t.Errorf("开局前 %q", rows["开局前"])
	}
	if len(e.Groups) == 0 {
		t.Error("应画出方案里的卡")
	}
}

// N65（G1 第 5 轮 11-27）：玩家在对话里同意覆盖（--overwrite），开局确认卡写明会换掉第几号编成里原来的卡；没同意不出这一行。
func TestExplainOverwriteConsentRow(t *testing.T) {
	h, p := host(), New()
	rowsOf := func(args map[string]any) map[string]string {
		e, ok := p.Explain(h, hiroapi.Pending{Command: "run start", Plan: planSnap(t, planJSON), Args: args})
		if !ok {
			t.Fatal("没认出")
		}
		rows := map[string]string{}
		for _, r := range e.Rows {
			rows[r[0]] = r[1]
		}
		return rows
	}
	base := func() map[string]any {
		return map[string]any{"kind": "hajime", "idol": "i_card-skin-ttmr-3-0012", "prepare": true,
			"prepare_note": "把编成方案写进回忆编成 13 号（你同意覆盖，原来的卡会被换掉）"}
	}
	with := base()
	with["overwrite"] = "memory=13"
	if got := rowsOf(with)["点名的编成位"]; !strings.Contains(got, "回忆编成 13 号") || !strings.Contains(got, "换掉") || strings.Contains(got, "支援卡") {
		t.Errorf("点名的编成位 %q", got)
	}
	if got, ok := rowsOf(base())["点名的编成位"]; ok {
		t.Errorf("没点名也出了这一行：%q", got)
	}
}

// N35（G1 第 3 轮 11-26）：写编成卡要写明写进第几号、会不会换掉原来的卡。
func TestWriteFormationCardNamesSets(t *testing.T) {
	h, p := host(), New()
	pd := hiroapi.Pending{Command: "run start", Plan: planSnap(t, planJSON), Access: "overwrite",
		Args: map[string]any{"kind": "write-formation", "scenario": "hajime", "support_set": 18, "memory_set": 12}}
	e, _ := p.Explain(h, pd)
	if len(e.Rows) != 1 || e.Rows[0][0] != "写进" || e.Rows[0][1] != "支援卡编成 18 号、回忆编成 12 号（原来有卡的位会被换掉）" {
		t.Errorf("写编成卡的编成号行 %+v", e.Rows)
	}
	// 没给编成号：用方案里的；只写回忆
	pd2 := hiroapi.Pending{Command: "run start", Plan: planSnap(t, `{"idol":{"name":"x"},"support":{"set":9,"own":[],"rental":null},"memory":{"set":8,"chosen":[]}}`),
		Access: "blank", Args: map[string]any{"kind": "write-formation", "scenario": "hajime", "what": "memory"}}
	e2, _ := p.Explain(h, pd2)
	if len(e2.Rows) != 1 || e2.Rows[0][1] != "回忆编成 8 号（都是空着的位）" {
		t.Errorf("只写回忆 %+v", e2.Rows)
	}
	// 只看差异：不出这一行
	e3, _ := p.Explain(h, hiroapi.Pending{Command: "run start", Args: map[string]any{"kind": "write-formation", "dry_run": true, "support_set": 3}})
	if len(e3.Rows) != 0 {
		t.Errorf("dry-run 不该有写进行 %+v", e3.Rows)
	}
}

// #122 review：日常选项的零值是不买、不抽（老用户照常点日常不该第一次就花金币）；不买时不传「另外要买的」；扭蛋次数有上限。
func TestDailyArgsDefaultSpendNothing(t *testing.T) {
	s := &state{}
	got := strings.Join(s.dailyArgs(), " ")
	if got != "--shop off --coin-gacha off" {
		t.Fatalf("缺省 %q", got)
	}
	s.shopItems = "再挑战券"
	if strings.Contains(strings.Join(s.dailyArgs(), " "), "shop-items") {
		t.Error("不买时不该传另外要买的")
	}
	s.shopMode, s.gachaMode, s.gachaN[0] = 1, 2, "999"
	got = strings.Join(s.dailyArgs(), " ")
	if !strings.Contains(got, "--shop recommended --shop-items 再挑战券") || !strings.Contains(got, fmt.Sprintf("--coin-gacha friend:%d", MaxGacha)) {
		t.Errorf("选了以后 %q", got)
	}
}

// CodeRabbit #122：偶像之路传偶像名（列表页按名字找路线），不传卡编号。
func TestTowerPassesBareIdolName(t *testing.T) {
	s := &state{}
	for i, m := range modes {
		if m.id == "tower" {
			s.mode = i
		}
	}
	s.pick.Chosen = common.IdolItem{ID: "i_card-skin-hski-3-001", Name: "【光景】篠泽广", Idol: "篠澤広"}
	a, _ := s.args()
	if a[len(a)-1] != "篠澤広" {
		t.Errorf("偶像之路参数 %q", a)
	}
	s.pick.Chosen.Idol = ""
	if a, _ := s.args(); a[len(a)-1] != "篠泽广" {
		t.Errorf("没有扫描名时去掉称号：%q", a)
	}
}

// 刷回忆：只有「初」；接着打时也带偶像卡和 --farm bad。
func TestFarmArgs(t *testing.T) {
	s := &state{}
	for i, m := range modes {
		if m.id == "hajime" {
			s.mode = i
		}
	}
	s.resume, s.farm, s.farmTarget = true, true, "p_card-02-act-3_186"
	if a, _ := s.args(); a != nil {
		t.Errorf("没选偶像卡不能发起：%q", a)
	}
	s.pick.Chosen = common.IdolItem{ID: "i_card-skin-hski-3-001", Name: "篠泽广"}
	a, label := s.args()
	got := strings.Join(a, " ")
	if !strings.Contains(got, "--farm bad --farm-target p_card-02-act-3_186 --idol i_card-skin-hski-3-001") || !strings.Contains(label, "刷回忆") {
		t.Errorf("%q %q", got, label)
	}
	s.farmTarget = ""
	if a, _ := s.args(); a != nil {
		t.Errorf("转写目标必填：%q", a)
	}
	// 审查 #198 第 8 条：程序不选租借回忆，刷回忆不能新开一局（按钮灰掉，提示先在游戏里开局再接着打）
	s.farmTarget, s.resume = "p_card-02-act-3_186", false
	if a, label := s.args(); a != nil || !strings.Contains(label, "接着打") {
		t.Errorf("刷回忆新开一局不能发起：%q %q", a, label)
	}
	s.resume = true
	s.farm = false
	if a, _ := s.args(); strings.Contains(strings.Join(a, " "), "farm") {
		t.Errorf("不勾不传：%q", a)
	}
}

// 审查 #198 第 8 条：上次是刷回忆（接着打）的局，首页不出「再打一把」（会变成刷回忆新开一局）；普通局照常。
func TestReplaySkipsFarm(t *testing.T) {
	j := plugin.Job{Kind: "hajime", Params: map[string]any{"command": "resume", "idol": "i_card-skin-hski-3-001", "difficulty": "regular", "farm": "bad", "farm_target": "輝くキミへ"}}
	if replay(j, "广", "初") != nil {
		t.Error("刷回忆的局不能按上次参数新开")
	}
	delete(j.Params, "farm")
	if replay(j, "广", "初") == nil {
		t.Error("普通局照常再打一把")
	}
}

// 每日免费刷新：缺省不传；勾了、又在买东西时才传 on。
func TestDailyArgsShopRefresh(t *testing.T) {
	s := &state{shopRefresh: true}
	if strings.Contains(strings.Join(s.dailyArgs(), " "), "shop-refresh") {
		t.Error("不买时不传免费刷新")
	}
	s.shopMode = 1
	if !strings.Contains(strings.Join(s.dailyArgs(), " "), "--shop-refresh on") {
		t.Errorf("勾了 %q", s.dailyArgs())
	}
}

// CodeRabbit #138 / #197：续打过的局（params 里有 resume_chain 并进来的 trace）再打一把：开新局，不带 --trace（run start 没这个参数）。
func TestReplayDropsTrace(t *testing.T) {
	j := plugin.Job{ID: "j1", Kind: "hif", Params: map[string]any{"command": "resume", "trace": "x-r1"}}
	run := replay(j, "篠澤広", "H.I.F")
	if run == nil {
		t.Fatal("应该能再打一把")
	}
	h := host()
	run(h)
	got := strings.Join(h.RunArgs_[len(h.RunArgs_)-1], " ")
	if strings.Contains(got, "--trace") || !strings.Contains(got, "--command new") {
		t.Errorf("参数 %q", got)
	}
	if j.Params["trace"] != "x-r1" {
		t.Error("不该改掉原局的参数")
	}
}

// CodeRabbit #197：旧的偶像之路记录 idol 是偶像卡编号（列表页按偶像名找）：不给「再打一把」。
func TestReplaySkipsOldTowerCardID(t *testing.T) {
	if replay(plugin.Job{ID: "j1", Kind: "tower", Params: map[string]any{"idol": "i_card-skin-hski-3-0001"}}, "x", "偶像之路") != nil {
		t.Error("旧记录不该给再打一把")
	}
	if replay(plugin.Job{ID: "j2", Kind: "tower", Params: map[string]any{"idol": "篠澤広"}}, "篠澤広", "偶像之路") == nil {
		t.Error("按偶像名记的应能再打一把")
	}
}
