package plugin

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"
)

func reg() *Registry {
	r := &Registry{}
	r.Add(&Plugin{ID: "start", Name: "开局", Tools: []string{"run start"},
		Commands: []Command{{Name: "开局", Hint: "开一局"}},
		Cards:    map[string]CardView{"confirm": func(*ui.Context, Host, Card) {}}})
	r.Add(&Plugin{ID: "library", Name: "资料库", Tools: []string{"info idols", "catalog search"},
		Commands: []Command{{Name: "资料库"}, {Name: "扫描"}},
		Cards:    map[string]CardView{"info": func(*ui.Context, Host, Card) {}}})
	return r
}

func TestRegistryFindsCardOwner(t *testing.T) {
	p, v := reg().CardView("info")
	if p == nil || p.ID != "library" || v == nil {
		t.Fatalf("info 卡归 %v", p)
	}
	if p, _ := reg().CardView("nobody"); p != nil {
		t.Errorf("没人认领的卡不该有主人：%v", p.ID)
	}
}

func TestCommandsMatchPrefix(t *testing.T) {
	got := reg().Commands("开")
	if len(got) != 1 || got[0].Command.Name != "开局" || got[0].Plugin.ID != "start" {
		t.Fatalf("开 → %+v", got)
	}
	if all := reg().Commands(""); len(all) != 3 {
		t.Errorf("空前缀列出全部，得到 %d 条", len(all))
	}
}

func TestToolOwner(t *testing.T) {
	r := reg()
	if r.ToolOwner("info idols") != "library" || r.ToolOwner("doctor") != "" {
		t.Error("工具认领不对")
	}
}

type nopHost struct{ Host }

func TestGatherSkipsSlowAndTruncates(t *testing.T) {
	r := &Registry{}
	r.Add(&Plugin{ID: "a", Name: "账号", Context: func(context.Context, Host) string { return "PLv 52" }})
	r.Add(&Plugin{ID: "slow", Name: "慢", Context: func(ctx context.Context, _ Host) string {
		<-ctx.Done()
		return "来不及"
	}})
	r.Add(&Plugin{ID: "empty", Name: "空", Context: func(context.Context, Host) string { return "" }})
	r.Add(&Plugin{ID: "long", Name: "长", Context: func(context.Context, Host) string { return strings.Repeat("字", 3000) }})
	now := time.Date(2026, 11, 24, 1, 30, 0, 0, time.Local)
	out := Gather(context.Background(), r, nopHost{}, now, 50*time.Millisecond)
	if !strings.HasPrefix(out, "## 现在的情况（11-24 01:30）") {
		t.Errorf("开头：%q", out[:min(60, len(out))])
	}
	if !strings.Contains(out, "### 账号\nPLv 52") {
		t.Error("没有账号")
	}
	if strings.Contains(out, "来不及") || strings.Contains(out, "### 慢") || strings.Contains(out, "### 空") {
		t.Error("慢的、空的不该出现")
	}
	if n := strings.Count(out, "字"); n != MaxContextRunes {
		t.Errorf("长文本截到 %d 字，要 %d", n, MaxContextRunes)
	}
	if strings.Index(out, "### 账号") > strings.Index(out, "### 长") {
		t.Error("按注册顺序")
	}
}

func TestGatherEmptyWhenNothing(t *testing.T) {
	if out := Gather(context.Background(), &Registry{}, nopHost{}, time.Now(), time.Second); out != "" {
		t.Errorf("没有上下文时返回空串，得到 %q", out)
	}
}

func TestFailReason(t *testing.T) {
	v := &JobView{State: "failed", Job: &Job{}, Events: []JobEvent{
		{Type: "log", Text: "[2026-11-23 12:42:18,555] [ERROR] [hiro.runtime.loop] 判页抛了异常"},
		{Type: "log", Text: "停下：点了 s_card-3-0027 但列表里没显示为选中（截图在 reports/live/calib/x）"},
		{Type: "failed", Text: "任务出错退出（代码 1），看上面的日志"},
	}}
	if r := v.FailReason(); r != "点了 s_card-3-0027 但列表里没显示为选中" {
		t.Errorf("从日志找原因：%q", r)
	}
	v.Events = append(v.Events, JobEvent{Type: "failed", Reason: "事件里的原因"})
	if r := v.FailReason(); r != "事件里的原因" {
		t.Errorf("failed 事件带的原因：%q", r)
	}
	v.Job.Reason = "任务上的原因"
	if r := v.FailReason(); r != "任务上的原因" {
		t.Errorf("任务上的原因：%q", r)
	}
	if r := (&JobView{State: "done"}).FailReason(); r != "" {
		t.Errorf("没出错不该有原因：%q", r)
	}
}
