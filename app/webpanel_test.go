package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPluginFilesServeInsideDirWithCSP(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "report.html"), []byte("<p>hi</p>"), 0o644); err != nil {
		t.Fatal(err)
	}
	h := pluginFiles(func(id string) string {
		if id == "demo" {
			return dir
		}
		return ""
	})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "hiroplug://localhost/demo/report.html", nil))
	if rec.Code != 200 || rec.Body.String() != "<p>hi</p>" {
		t.Fatalf("插件页：%d %q", rec.Code, rec.Body.String())
	}
	if csp := rec.Header().Get("Content-Security-Policy"); csp != PluginCSP {
		t.Errorf("CSP %q", csp)
	}
	for _, p := range []string{"/demo/../../etc/passwd", "/other/report.html", "/demo/missing.html", "/demo/%2e%2e/x"} {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", "hiroplug://localhost"+p, nil))
		if rec.Code != 404 {
			t.Errorf("%s 应 404，得到 %d", p, rec.Code)
		}
	}
}

func TestWebURL(t *testing.T) {
	if got := webURL("hiro", "review/j7.html", "darwin"); got != "hiro://localhost/review/j7.html" {
		t.Errorf("mac：%q", got)
	}
	if got := webURL("hiroplug", "demo/report.html", "windows"); got != "http://hiroplug.localhost/demo/report.html" {
		t.Errorf("windows：%q", got)
	}
}

func TestPluginFileURLFindsPluginDir(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "plugins", "demo")
	dirOf := func(id string) string {
		if id == "demo" {
			return dir
		}
		return ""
	}
	got, owner := pluginFileURL(dirOf, filepath.Join(dir, "web", "report.html"))
	if got != webURL("hiroplug", "demo/web/report.html", runtimeGOOS()) || owner != "demo" {
		t.Errorf("地址 %q %q", got, owner)
	}
	if u, _ := pluginFileURL(dirOf, "/etc/passwd"); u != "" {
		t.Error("插件目录外的文件不给地址")
	}
}

// CR#14：插件 A 的网页不能读插件 B 目录里的文件。
func TestPluginFilesOnlyForOwnPage(t *testing.T) {
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "r.html"), []byte("b"), 0o644)
	h := pluginFilesFor(func(id string) string {
		if id == "b" {
			return dir
		}
		return ""
	}, func(*http.Request) string { return "a" })
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "hiroplug://localhost/b/r.html", nil))
	if rec.Code != 404 {
		t.Errorf("A 的页面读到了 B 的文件：%d", rec.Code)
	}
}

// B4（11-28 rc.12 真窗口）：插件网页面板叫 index.html 时 http.ServeFile 会 301 跳到目录，WebKit 自定义协议不跟跳转 → 空白。
// 要直接给内容；目录本身不给。
func TestPluginFilesServeIndexHTMLWithoutRedirect(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "index.html"), []byte("<h2>演示</h2>"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	h := pluginFiles(func(id string) string {
		if id == "demo" {
			return dir
		}
		return ""
	})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "hiroplug://localhost/demo/index.html", nil))
	if rec.Code != 200 || rec.Body.String() != "<h2>演示</h2>" || !strings.HasPrefix(rec.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("index.html 要直接给内容：%d %q %q", rec.Code, rec.Header().Get("Content-Type"), rec.Body.String())
	}
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "hiroplug://localhost/demo/sub", nil))
	if rec.Code != 404 {
		t.Errorf("目录不给：%d", rec.Code)
	}
}
