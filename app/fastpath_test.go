package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// 状态和事件直接问 hiro 服务（本机端口 + 令牌），不再每次起一个 Python 进程。
func TestStatusAndEventsGoStraightToTheService(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Hiro-Token") != "tok" {
			w.WriteHeader(401)
			return
		}
		b, _ := io.ReadAll(r.Body)
		paths = append(paths, r.URL.Path+" "+string(b))
		_, _ = io.WriteString(w, `{"ok":true,"data":{"current":null,"recent":[]}}`)
	}))
	defer srv.Close()
	home := t.TempDir()
	port := srv.URL[strings.LastIndex(srv.URL, ":")+1:]
	info, _ := json.Marshal(map[string]any{"port": mustAtoi(port), "token": "tok", "pid": 1})
	_ = os.WriteFile(filepath.Join(home, "service.json"), info, 0o600)
	t.Setenv("HIRO_HOME", home)
	// 进程那条路会失败：证明没走它
	h := &Hiro{Command: []string{filepath.Join(home, "no-such-hiro")}}
	r := h.Call(context.Background(), []string{"run", "status"})
	if !r.OK || r.Exit != 0 {
		t.Fatalf("status %+v", r)
	}
	r = h.Call(context.Background(), []string{"run", "events", "--job", "j1", "--since", "7"})
	if !r.OK {
		t.Fatalf("events %+v", r)
	}
	if len(paths) != 2 || paths[0] != "/status {}" || !strings.Contains(paths[1], `"job":"j1"`) || !strings.Contains(paths[1], `"since":7`) {
		t.Fatalf("paths %v", paths)
	}
}

func TestFallsBackToTheCommandWhenNoService(t *testing.T) {
	t.Setenv("HIRO_HOME", t.TempDir())
	h := &Hiro{Command: fakeHiro(t)}
	r := h.Call(context.Background(), []string{"ok", "x"})
	if !r.OK {
		t.Fatalf("%+v", r)
	}
}

func mustAtoi(s string) int { n, _ := strconv.Atoi(s); return n }
