package shell

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 11-25 review：参考 dsh 的权限范围——总是询问 / 允许读操作 / 允许读写操作 / 允许所有行为

func TestAllows(t *testing.T) {
	cases := []struct {
		level, access string
		want          bool
	}{
		{PermAsk, "read", false}, {PermAsk, "daily", false},
		{PermRead, "read", true}, {PermRead, "daily", true}, {PermRead, "blank", true},
		{PermRead, "write", false}, {PermRead, "overwrite", false}, {PermRead, "all", false},
		{PermReadWrite, "write", true}, {PermReadWrite, "overwrite", true}, {PermReadWrite, "all", false},
		{PermAll, "all", true}, {PermAll, "", true},
		{PermRead, "", false}, // 不知道类别（旧版 CLI）按最高的算
		{"", "read", true},    // 没设过 = 允许读操作
	}
	for _, c := range cases {
		if got := Allows(c.level, c.access); got != c.want {
			t.Errorf("Allows(%q, %q) = %v", c.level, c.access, got)
		}
	}
}

func permApp(t *testing.T, level string, cards ...plugin.Card) (*App, *SyncRunner, *fakeCall, *ui.Tester) {
	t.Helper()
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"confirm p1": ok(`{"job":{"id":"j1","kind":"scan-memories"}}`),
	}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "好", Cards: cards}, nil
	}
	a, run := newTestApp(newMem(), call, ask, viewReg())
	a.d.Permission = func() string { return level }
	tt := ui.NewTester(a.View, 1280, 800)
	a.Send("扫描回忆")
	run.Drain()
	tt.Frame()
	return a, run, call, tt
}

func accessPending(id, kind, access string) hiroapi.Pending {
	p := pend(id, kind, "做 "+kind)
	p.Access = access
	return p
}

func TestReadAutoStartsUnderDefault(t *testing.T) {
	a, _, call, tt := permApp(t, "", ConfirmCard(accessPending("p1", "scan-memories", "read")))
	if !call.called("confirm p1") {
		t.Fatalf("读操作在缺省权限下应自动开始：%v", call.calls)
	}
	card := lastCardsWith(a, "confirm")[0]
	if card["status"] != "confirmed" || card["auto"] != PermRead {
		t.Errorf("卡 %+v", card)
	}
	if all := strings.Join(tt.Texts(), "\n"); !strings.Contains(all, "自动开始") || !strings.Contains(all, "允许读操作") {
		t.Errorf("没写明是按权限自动开始的：%q", tt.Texts())
	}
}

func TestAskLevelAlwaysWaits(t *testing.T) {
	_, _, call, tt := permApp(t, PermAsk, ConfirmCard(accessPending("p1", "scan-memories", "read")))
	if call.called("confirm p1") || !tt.HasText("先不") {
		t.Errorf("总是询问时不该自动开始：%v", call.calls)
	}
}

func TestProduceWaitsUnlessAll(t *testing.T) {
	_, _, call, _ := permApp(t, PermReadWrite, ConfirmCard(accessPending("p1", "hajime", "all")))
	if call.called("confirm p1") {
		t.Error("允许读写时开局仍要确认")
	}
	_, _, call, _ = permApp(t, PermAll, ConfirmCard(accessPending("p1", "hajime", "all")))
	if !call.called("confirm p1") {
		t.Error("允许所有行为时开局直接开始")
	}
}

func TestBatchAutoOnlyIfEveryItemAllowed(t *testing.T) {
	_, _, call, _ := permApp(t, PermRead, ConfirmCard(accessPending("p1", "scan-memories", "read")), ConfirmCard(accessPending("p2", "hajime", "all")))
	if call.called("confirm p1") {
		t.Error("合并卡里有一项要确认，整张都要等")
	}
}

func TestPluginConfirmNeverAuto(t *testing.T) {
	c := ConfirmCard(accessPending("p1", "scan-memories", "read"))
	c["from"] = "某插件"
	_, _, call, _ := permApp(t, PermAll, c)
	if call.called("confirm p1") {
		t.Error("外部插件发起的确认卡一律要用户点")
	}
}

// R11：第一次用（向导走完）时，从没扫过的库存自己发起扫描，读操作在缺省权限下不用确认
func TestFirstScanStartsUnscannedKinds(t *testing.T) {
	p1, p2 := accessPending("p1", "scan-supports", "read"), accessPending("p2", "scan-idols", "read")
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"info summary":            ok(`{"kinds":{"supports":{"count":0,"scanned_at":null},"memories":{"count":40,"scanned_at":"2026-11-18 20:00:00"},"idols":{"count":0,"scanned_at":null}}}`),
		"run start scan-supports": {OK: true, Pending: &p1},
		"run start scan-idols":    {OK: true, Pending: &p2},
		"confirm p1":              ok(`{"job":{"id":"j1","kind":"scan-supports"}}`),
	}}
	a, run := newTestApp(newMem(), call, nil, viewReg())
	a.FirstScan()
	run.Drain()
	if call.called("run start scan-memories") {
		t.Error("扫过的不再扫")
	}
	if !call.called("run start scan-supports") || !call.called("run start scan-idols") || !call.called("confirm p1") {
		t.Fatalf("没扫过的应自动开始：%v", call.calls)
	}
	card := lastCardsWith(a, "confirm")[0]
	if b, _ := card["batch"].([]any); len(b) != 2 || card["auto"] != PermRead {
		t.Errorf("首次扫描卡 %+v", card)
	}
}

// CodeRabbit #79：同一条回复里有选择卡（还没选偶像）时，确认卡不自动开始，哪怕是「允许所有行为」
func TestNoAutoStartWhileChoosing(t *testing.T) {
	choice := plugin.Card{"type": "choice", "kind": "idols", "status": "waiting", "options": []any{map[string]any{"id": "i_card-a"}}}
	_, _, call, _ := permApp(t, PermAll, choice, ConfirmCard(accessPending("p1", "hajime", "all")))
	if call.called("confirm p1") {
		t.Error("还在选偶像时不该自动开局")
	}
}

// CodeRabbit #79：按权限自动开始的合并卡，后面的项重新发起时要按当前档位再查一次（扫完编成位后写编成可能从空白变成覆盖）
func TestQueuedItemRecheckedWhenAutoStarted(t *testing.T) {
	over := accessPending("p9", "write-formation", "overwrite")
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"confirm p1":                ok(`{"job":{"id":"j1","kind":"scan-formations"}}`),
		"run start write-formation": {OK: true, Pending: &over},
		"run status":                ok(`{"current":{"id":"j1","kind":"scan-formations","state":"running","summary":"读编成位","started_at":1},"recent":[]}`),
		"run events":                ok(`{"state":"running","events":[]}`),
	}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "好", Cards: []plugin.Card{ConfirmCard(accessPending("p1", "scan-formations", "read")), ConfirmCard(accessPending("p2", "write-formation", "blank"))}}, nil
	}
	a, run := newTestApp(newMem(), call, ask, viewReg())
	a.Send("读编成位再写")
	run.Drain()
	if !call.called("confirm p1") {
		t.Fatal("两项都允许，应自动开始")
	}
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j1","kind":"scan-formations","state":"done","summary":"读编成位","started_at":1}]}`)
	call.resp["run events"] = ok(`{"state":"done","events":[]}`)
	a.PollJobs()
	run.Drain()
	if call.called("confirm p9") {
		t.Fatal("变成覆盖后不该再自动确认")
	}
	waiting := false
	for _, m := range a.Current().Messages {
		for _, c := range m.Cards {
			if a.waiting(c) {
				waiting = true
			}
		}
	}
	if !waiting {
		t.Error("应留一张等确认的卡")
	}
}

// 首次扫描有一项发不起来：要告诉用户
func TestFirstScanReportsErrors(t *testing.T) {
	p1 := accessPending("p1", "scan-supports", "read")
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"info summary":            ok(`{"kinds":{"supports":{"scanned_at":null},"memories":{"scanned_at":"2026-11-18 20:00:00"},"idols":{"scanned_at":null}}}`),
		"run start scan-supports": {OK: true, Pending: &p1},
		"run start scan-idols":    {OK: false, Error: &hiroapi.CliError{Code: "service_down", Message: "游戏没连上", Hint: "到设置里试连"}},
		"confirm p1":              ok(`{"job":{"id":"j1","kind":"scan-supports"}}`),
	}}
	a, run := newTestApp(newMem(), call, nil, viewReg())
	a.FirstScan()
	run.Drain()
	errs := 0
	for _, c := range lastCardsWith(a, "error") {
		if c["type"] == "error" {
			errs++
		}
	}
	if errs != 1 {
		t.Errorf("没把发不起来的那项告诉用户：%+v", a.Current().Messages)
	}
}

// 11-25 review：「这个设置，我们放进对话框里吧，ui 同 codex」——输入框左下角就是当前档位，点开换档。
func composerPermApp(t *testing.T, level string, confirmed bool) (*App, *ui.Tester, *[]string) {
	t.Helper()
	a, _ := newTestApp(newMem(), &fakeCall{}, nil, viewReg())
	saved := &[]string{}
	a.d.Permission = func() string { return level }
	a.d.AllowAllConfirmed = func() bool { return confirmed }
	a.d.SetPermission = func(l string) error {
		*saved = append(*saved, l)
		level = l
		if l == PermAll {
			confirmed = true
		}
		return nil
	}
	tt := ui.NewTester(a.View, 1280, 800)
	tt.Frame()
	return a, tt, saved
}

func TestComposerShowsCurrentPermission(t *testing.T) {
	_, tt, _ := composerPermApp(t, "", false)
	if !tt.HasText("允许读操作") {
		t.Fatalf("输入框里应看得到当前档位：%q", tt.Texts())
	}
}

func TestComposerSwitchesPermission(t *testing.T) {
	_, tt, saved := composerPermApp(t, PermRead, false)
	if err := tt.Click("操作权限：" + PermName[PermRead]); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if err := tt.Click("允许读写操作"); err != nil {
		t.Fatalf("菜单里没有「允许读写操作」：%v %q", err, tt.Texts())
	}
	tt.Frame()
	if len(*saved) != 1 || (*saved)[0] != PermReadWrite {
		t.Fatalf("应存成 readwrite：%v", *saved)
	}
	if !tt.HasText("允许读写操作") {
		t.Errorf("换档后输入框里应显示新档位：%q", tt.Texts())
	}
}

func TestComposerAllowAllAsksOnce(t *testing.T) {
	_, tt, saved := composerPermApp(t, PermRead, false)
	if err := tt.Click("操作权限：" + PermName[PermRead]); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	_ = tt.Click("允许所有行为")
	tt.Frame()
	if len(*saved) != 0 || !tt.HasText("确定允许所有行为？") {
		t.Fatalf("第一次切到允许所有行为要先二次确认：%v %q", *saved, tt.Texts())
	}
	if err := tt.Click("确定，允许所有行为"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	if len(*saved) != 1 || (*saved)[0] != PermAll {
		t.Fatalf("确认后应存成 all：%v", *saved)
	}
	// 确认过一次，之后直接切
	_, tt2, saved2 := composerPermApp(t, PermRead, true)
	_ = tt2.Click("操作权限：" + PermName[PermRead])
	tt2.Frame()
	_ = tt2.Click("允许所有行为")
	tt2.Frame()
	if len(*saved2) != 1 || tt2.HasText("确定允许所有行为？") {
		t.Errorf("确认过的不再弹：%v", *saved2)
	}
}

func TestPreviewComposerPermission(t *testing.T) {
	_, tt, _ := composerPermApp(t, PermRead, false)
	snap(t, tt, "perm-composer")
	_ = tt.Click("操作权限：" + PermName[PermRead])
	tt.Frame()
	snap(t, tt, "perm-composer-menu")
}

// CodeRabbit #91：存不上（文件不可写等）时要说清楚、能再试，不能当作已经换档；确认框存成功才关。
func TestComposerPermissionSaveErrorShown(t *testing.T) {
	a, tt, _ := composerPermApp(t, PermAll, true)
	a.d.SetPermission = func(string) error { return errors.New("settings.json 写不进去") }
	_ = tt.Click("操作权限：" + PermName[PermAll])
	tt.Frame()
	_ = tt.Click("总是询问")
	tt.Frame()
	if !tt.HasText("操作权限没存上：settings.json 写不进去。现在还是「允许所有行为」，再选一次试试") {
		t.Errorf("保存失败要说原因和下一步：%q", tt.Texts())
	}
	a2, tt2, _ := composerPermApp(t, PermRead, false)
	a2.d.SetPermission = func(string) error { return errors.New("磁盘满了") }
	_ = tt2.Click("操作权限：" + PermName[PermRead])
	tt2.Frame()
	_ = tt2.Click("允许所有行为")
	tt2.Frame()
	_ = tt2.Click("确定，允许所有行为")
	tt2.Frame()
	if !tt2.HasText("确定允许所有行为？") || !tt2.HasText("没存上：磁盘满了，再点一次试试") {
		t.Errorf("确认框存失败不能关，要说原因：%q", tt2.Texts())
	}
}

// N6（11-26 G1）：自动开始了，回复里就不该还留着「要开始「…」吗？点下面的确认卡。」
func TestAutoStartDropsAskSentence(t *testing.T) {
	call := &fakeCall{resp: map[string]hiroapi.Result{"confirm p1": ok(`{"job":{"id":"j1","kind":"scan-idols"}}`)}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "好的，再扫一次。\n要开始「扫描偶像卡」吗？点下面的确认卡。", Cards: []plugin.Card{ConfirmCard(accessPending("p1", "scan-idols", "read"))}}, nil
	}
	a, run := newTestApp(newMem(), call, ask, viewReg())
	a.d.Permission = func() string { return "" }
	a.Send("再扫一次偶像卡")
	run.Drain()
	for _, m := range a.Current().Messages {
		if strings.Contains(m.Text, "点下面的确认卡") {
			t.Errorf("自动开始后还在叫人点确认卡：%q", m.Text)
		}
	}
	found := false
	for _, m := range a.Current().Messages {
		found = found || m.Text == "好的，再扫一次。"
	}
	if !found {
		t.Error("模型自己说的话要留着")
	}
	// 要等确认的照旧
	ask2 := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "要开始「开一局初」吗？点下面的确认卡。", Cards: []plugin.Card{ConfirmCard(accessPending("p2", "hajime", "all"))}}, nil
	}
	b, run2 := newTestApp(newMem(), &fakeCall{resp: map[string]hiroapi.Result{}}, ask2, viewReg())
	b.d.Permission = func() string { return "" }
	b.Send("打一把初")
	run2.Drain()
	ms := b.Current().Messages
	if !strings.Contains(ms[len(ms)-1].Text, "点下面的确认卡") {
		t.Errorf("要确认的还应提示点确认卡：%q", ms[len(ms)-1].Text)
	}
}
