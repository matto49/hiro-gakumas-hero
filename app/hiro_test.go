package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// fakeHiro 写一个假的 hiro 命令：按第一个参数打印不同的信封。
func fakeHiro(t *testing.T) []string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("假命令用 sh 脚本")
	}
	p := filepath.Join(t.TempDir(), "hiro")
	script := `#!/bin/sh
case "$1" in
  ok) echo '{"ok":true,"data":{"args":"'"$*"'"}}' ;;
  fail) echo '{"ok":false,"error":{"code":"busy","message":"已经有任务在跑","hint":"等它结束"}}'; exit 1 ;;
  pending) echo '{"ok":true,"data":{"pending":{"id":"abc","command":"run start","summary":"开一局","expires_at":1}}}'; exit 3 ;;
  garbage) echo 'Traceback: boom' ; exit 1 ;;
  pytb) printf 'Traceback (most recent call last):\n  File "usage_report.py", line 40, in _multiplier\n    t = x\nzoneinfo._common.ZoneInfoNotFoundError: No time zone found with key Asia/Shanghai\n' >&2; exit 1 ;;
esac
`
	if err := os.WriteFile(p, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return []string{p}
}

func TestCallParsesOkEnvelope(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"ok", "x"})
	if !r.OK || r.Exit != 0 {
		t.Fatalf("want ok, got %+v", r)
	}
	var d map[string]string
	if err := json.Unmarshal(r.Data, &d); err != nil || d["args"] != "ok x" {
		t.Fatalf("data = %s (%v)", r.Data, err)
	}
}

func TestCallKeepsErrorCodeAndHint(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"fail"})
	if r.OK || r.Error == nil || r.Error.Code != "busy" || r.Error.Hint != "等它结束" || r.Exit != 1 {
		t.Fatalf("got %+v", r)
	}
}

func TestCallReportsPending(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"pending"})
	if !r.OK || r.Exit != 3 || r.Pending == nil || r.Pending.ID != "abc" {
		t.Fatalf("got %+v", r)
	}
}

func TestCallTurnsGarbageIntoAnError(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"garbage"})
	if r.OK || r.Error == nil || r.Error.Code != "bad_output" {
		t.Fatalf("got %+v", r)
	}
}

func TestCallWithoutHiroExplains(t *testing.T) {
	h := &Hiro{Command: []string{filepath.Join(t.TempDir(), "missing")}}
	r := h.Call(context.Background(), []string{"ok"})
	if r.OK || r.Error.Code != "hiro_missing" || r.Error.Hint == "" {
		t.Fatalf("got %+v", r)
	}
}

func TestCallShortensPythonTraceback(t *testing.T) {
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"pytb"})
	if r.OK || r.Error == nil || r.Error.Code != "bad_output" {
		t.Fatalf("got %+v", r)
	}
	want := "hiro 工具层出错了：zoneinfo._common.ZoneInfoNotFoundError: No time zone found with key Asia/Shanghai"
	if r.Error.Message != want {
		t.Errorf("message = %q，要 %q", r.Error.Message, want)
	}
	if strings.Contains(r.Error.Message, "File \"") {
		t.Errorf("traceback 正文不该露给用户：%q", r.Error.Message)
	}
}

func TestBadOutputMessageKeepsPlainText(t *testing.T) {
	if got := badOutputMessage("boom"); got != "hiro 工具层输出看不懂：boom" {
		t.Errorf("got %q", got)
	}
}
