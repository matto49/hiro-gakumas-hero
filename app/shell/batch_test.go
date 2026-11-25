package shell

import (
	"context"
	"strings"
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 11-25 review：一条回复禁止出两张确认卡；多条待确认合进一张卡（像 codex 那样逐项列出）。同一种信息卡只留最后一张。

func pend(id, kind, summary string) hiroapi.Pending {
	return hiroapi.Pending{ID: id, Command: "run start", Summary: summary, ExpiresAt: 4102444800, Args: map[string]any{"kind": kind, "dry_run": false, "idol": nil}}
}

func batchApp(t *testing.T) (*App, *SyncRunner, *fakeCall, *ui.Tester) {
	t.Helper()
	call := &fakeCall{resp: map[string]hiroapi.Result{
		"confirm p1":                ok(`{"job":{"id":"j1","kind":"scan-memories"}}`),
		"run start write-formation": {OK: true, Data: []byte(`{"pending":{"id":"p9","command":"run start","summary":"写编成"}}`), Pending: &hiroapi.Pending{ID: "p9", Command: "run start", Summary: "写编成"}},
		"confirm p9":                ok(`{"job":{"id":"j2","kind":"write-formation"}}`),
		"run status":                ok(`{"current":{"id":"j1","kind":"scan-memories","state":"running","summary":"扫描回忆","started_at":1},"recent":[]}`),
		"run events":                ok(`{"state":"running","events":[]}`),
	}}
	ask := func(context.Context, []Turn, string, string, func(string, string)) (Reply, error) {
		return Reply{Text: "先扫描回忆，再写编成：", Cards: []plugin.Card{
			{"type": "info", "kind": "idols", "items": []any{"a", "b", "c"}},
			ConfirmCard(pend("p1", "scan-memories", "扫描回忆")),
			{"type": "info", "kind": "idols", "items": []any{"a"}},
			ConfirmCard(pend("p2", "write-formation", "写编成")),
		}}, nil
	}
	a, run := newTestApp(newMem(), call, ask, viewReg())
	tt := ui.NewTester(a.View, 1280, 900)
	a.Send("扫描回忆再写编成")
	run.Drain()
	tt.Frame()
	return a, run, call, tt
}

func TestReplyMergesConfirmCards(t *testing.T) {
	a, _, _, tt := batchApp(t)
	cards := lastCards(a)
	confirms, infos := 0, 0
	for _, c := range cards {
		switch c["type"] {
		case "confirm":
			confirms++
			if b, _ := c["batch"].([]any); len(b) != 2 {
				t.Errorf("合并后的确认卡应有 2 项：%+v", c)
			}
		case "info":
			infos++
			if items, _ := c["items"].([]any); len(items) != 1 {
				t.Errorf("同种信息卡应只留最后一张：%+v", c)
			}
		}
	}
	if confirms != 1 || infos != 1 {
		t.Fatalf("确认卡 %d 张、信息卡 %d 张：%+v", confirms, infos, cards)
	}
	for _, s := range []string{"扫描回忆", "写编成", "全部开始", "先不"} {
		if !tt.HasText(s) {
			t.Errorf("合并的确认卡没有 %q：%q", s, tt.Texts())
		}
	}
	snap(t, tt, "confirm-batch")
}

func TestBatchRunsInOrder(t *testing.T) {
	a, run, call, tt := batchApp(t)
	if err := tt.Click("全部开始"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	if !call.called("confirm p1") || !call.called("cancel p2") {
		t.Fatalf("先确认第 1 项、退掉后面的待确认：%v", call.calls)
	}
	if call.called("run start") {
		t.Fatal("第 1 项还没做完，不该开第 2 项")
	}
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j1","kind":"scan-memories","state":"done","summary":"扫描回忆","started_at":1}]}`)
	call.resp["run events"] = ok(`{"state":"done","events":[]}`)
	a.PollJobs()
	run.Drain()
	if !call.called("run start write-formation") || !call.called("confirm p9") {
		t.Fatalf("第 1 项做完后应发起并确认第 2 项：%v", call.calls)
	}
	runs := 0
	for _, c := range lastCardsWith(a, "confirm") {
		if c["type"] == "run" {
			runs++
		}
	}
	if runs != 2 {
		t.Errorf("应有两张对局卡：%d", runs)
	}
}

func TestBatchStopsWhenItemFails(t *testing.T) {
	a, run, call, tt := batchApp(t)
	_ = tt.Click("全部开始")
	run.Drain()
	a.PollJobs()
	run.Drain()
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j1","kind":"scan-memories","state":"failed","summary":"扫描回忆","started_at":1,"reason":"游戏掉线"}]}`)
	call.resp["run events"] = ok(`{"state":"failed","events":[]}`)
	a.PollJobs()
	run.Drain()
	if call.called("run start") {
		t.Fatal("第 1 项出错，后面的不该再做")
	}
	found := false
	for _, m := range a.Current().Messages {
		if strings.Contains(m.Text, "后面 1 项没做") {
			found = true
		}
	}
	if !found {
		t.Error("没告诉用户后面几项没做")
	}
}

func TestBatchRemoveItem(t *testing.T) {
	a, run, call, tt := batchApp(t)
	if err := tt.Click("去掉第 2 项"); err != nil {
		t.Fatal(err)
	}
	run.Drain()
	if !call.called("cancel p2") {
		t.Fatalf("去掉一项要取消它的待确认：%v", call.calls)
	}
	for _, c := range lastCards(a) {
		if c["type"] == "confirm" {
			if b, _ := c["batch"].([]any); len(b) != 1 {
				t.Errorf("去掉后剩 1 项：%+v", c)
			}
		}
	}
}

// lastCardsWith 是含某种卡的最后一条消息的卡片。
func lastCardsWith(a *App, typ string) []plugin.Card {
	ms := a.Current().Messages
	for i := len(ms) - 1; i >= 0; i-- {
		for _, c := range ms[i].Cards {
			if c["type"] == typ {
				return ms[i].Cards
			}
		}
	}
	return nil
}

// CodeRabbit #79：任务在第一次轮询前就结束（开局即失败），也要续做排队项 / 发结束通知
func TestQuickFailStillNoticedAndStopsQueue(t *testing.T) {
	a, run, call, tt := batchApp(t)
	call.resp["run status"] = ok(`{"current":null,"recent":[{"id":"j1","kind":"scan-memories","state":"failed","summary":"扫描回忆","started_at":1,"reason":"游戏掉线"}]}`)
	call.resp["run events"] = ok(`{"state":"failed","events":[]}`)
	_ = tt.Click("全部开始")
	run.Drain()
	a.PollJobs()
	run.Drain()
	var texts []string
	for _, m := range a.Current().Messages {
		texts = append(texts, m.Text)
	}
	all := strings.Join(texts, "\n")
	if !strings.Contains(all, "后面 1 项没做") || !strings.Contains(all, "出错停下了：游戏掉线") {
		t.Errorf("开局即失败也要通知、停队列：%q", texts)
	}
}

// 「全部开始」请求还没回来时不能去掉项（会让排队错位）
func TestRemoveIgnoredWhileResolving(t *testing.T) {
	a, _, call, _ := batchApp(t)
	card := lastCardsWith(a, "confirm")[0]
	if a.ui.resolving == nil {
		a.ui.resolving = map[string]bool{}
	}
	a.ui.resolving["p1"] = true
	a.removeBatchItem(a.Current(), card, 1)
	if call.called("cancel p2") || len(batchOf(card)) != 2 {
		t.Error("确认进行中不该能去掉项")
	}
}

// CodeRabbit #79：「全部开始」第 1 项就没开起来：退掉其余待确认，告诉用户哪几项没做
func TestBatchFirstItemFails(t *testing.T) {
	a, run, call, tt := batchApp(t)
	call.resp["confirm p1"] = hiroapi.Result{OK: false, Error: &hiroapi.CliError{Code: "service_down", Message: "游戏没连上"}}
	_ = tt.Click("全部开始")
	run.Drain()
	if !call.called("cancel p2") {
		t.Errorf("要退掉第 2 项的待确认：%v", call.calls)
	}
	found := false
	for _, m := range a.Current().Messages {
		if strings.Contains(m.Text, "后面 1 项没做") {
			found = true
		}
	}
	if !found {
		t.Error("没告诉用户后面几项没做")
	}
}
