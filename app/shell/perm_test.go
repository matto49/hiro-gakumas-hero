package shell

import (
	"context"
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
