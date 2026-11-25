package hiroapi

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestResultJSONFieldNames(t *testing.T) {
	r := Result{OK: true, Data: json.RawMessage(`{"a":1}`), Pending: &Pending{ID: "p1", Command: "hiro run start daily", Summary: "领日常", ExpiresAt: 12}, Exit: PendingExit}
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{`"ok":true`, `"data":{"a":1}`, `"pending":{"id":"p1"`, `"expires_at":12`, `"exit":3`} {
		if !strings.Contains(string(b), k) {
			t.Errorf("%s 里没有 %s", b, k)
		}
	}
	var back Result
	if err := json.Unmarshal([]byte(`{"ok":false,"error":{"code":"x","message":"坏了","hint":"重试"}}`), &back); err != nil || back.Error == nil || back.Error.Hint != "重试" {
		t.Fatalf("读回 %+v %v", back, err)
	}
}

// 待确认里固定的方案快照要跟着确认卡存下来、再读回来（PR #79 CodeRabbit：确认卡方案快照）
func TestPendingPlanSnapshotRoundTrip(t *testing.T) {
	raw := `{"id":"abc","command":"run start","summary":"写编成","expires_at":9,"plan":{"support":{"set":2}}}`
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatal(err)
	}
	p, ok := PendingFromMap(m)
	if !ok || p.Plan["support"] == nil {
		t.Fatalf("读不出 plan：%+v", p)
	}
	back, _ := PendingFromMap(p.Map())
	if back.Plan["support"] == nil {
		t.Errorf("存进卡片再读回来丢了 plan：%+v", p.Map())
	}
	if _, has := (Pending{ID: "x"}).Map()["plan"]; has {
		t.Error("没有快照时不该写 plan")
	}
}
