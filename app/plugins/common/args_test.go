package common

import (
	"strings"
	"testing"
)

func TestStartArgv(t *testing.T) {
	got := strings.Join(StartArgv("hif", map[string]any{"command": "new", "dry_run": false, "layers": float64(3), "idol": nil, "no_llm": true, "what": ""}), " ")
	if got != "run start hif --command new --layers 3 --no-llm" {
		t.Errorf("%q", got)
	}
}
