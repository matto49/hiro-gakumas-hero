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
