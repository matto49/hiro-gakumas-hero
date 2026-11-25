package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const fakeCommit = "0123456789abcdef0123456789abcdef01234567"

type tarFile struct {
	name, body string
	mode       int64
}

// makeArchive 造一个和 GitHub 归档同样结构的 tar.gz：pax 全局头（comment 是提交号）+ 一层顶级目录。
func makeArchive(t *testing.T, top, commit string, files ...tarFile) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	if commit != "" {
		if err := tw.WriteHeader(&tar.Header{Typeflag: tar.TypeXGlobalHeader, Name: "pax_global_header", PAXRecords: map[string]string{"comment": commit}}); err != nil {
			t.Fatal(err)
		}
	}
	if err := tw.WriteHeader(&tar.Header{Typeflag: tar.TypeDir, Name: top + "/", Mode: 0o755}); err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		mode := f.mode
		if mode == 0 {
			mode = 0o644
		}
		if err := tw.WriteHeader(&tar.Header{Typeflag: tar.TypeReg, Name: top + "/" + f.name, Mode: mode, Size: int64(len(f.body))}); err != nil {
			t.Fatal(err)
		}
		_, _ = tw.Write([]byte(f.body))
	}
	_ = tw.Close()
	_ = gz.Close()
	return buf.Bytes()
}

var repoFiles = []tarFile{
	{name: "pyproject.toml", body: "[project]\n"},
	{name: "scripts/setup.sh", body: "#!/bin/sh\n", mode: 0o755},
	{name: "src/hiro/__init__.py", body: ""},
}

// fakeGitHub 起一个假的 GitHub：routes 里有的路径返回对应字节，其余 404；hits 记下请求过的路径。
type fakeGitHub struct {
	*httptest.Server
	mu   sync.Mutex
	hits []string
}

func newFakeGitHub(t *testing.T, routes map[string][]byte) *fakeGitHub {
	t.Helper()
	f := &fakeGitHub{}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.hits = append(f.hits, r.URL.Path)
		f.mu.Unlock()
		b, ok := routes[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write(b)
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeGitHub) hitList() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return strings.Join(f.hits, ",")
}

func spec(f *fakeGitHub, home, tag string) SourceSpec {
	return SourceSpec{Repo: f.URL, Tag: tag, Dest: filepath.Join(home, "src"), Client: f.Client(), Backoff: time.Millisecond}
}

func readSourceInfo(t *testing.T, dest string) map[string]string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dest, sourceInfoFile))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]string
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func noLeftovers(t *testing.T, home string) {
	t.Helper()
	ents, _ := os.ReadDir(home)
	for _, e := range ents {
		if e.Name() != "src" {
			t.Fatalf("Home 里留下了 %s", e.Name())
		}
	}
}

func TestFetchSourceDownloadsTheTagOfThisVersionAndStripsTheTopDir(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/tags/desktop-v0.1.1.tar.gz": makeArchive(t, "hiro-gakumas-hero-desktop-v0.1.1", fakeCommit, repoFiles...),
	})
	home := t.TempDir()
	var said []string
	if err := FetchSource(context.Background(), spec(gh, home, "desktop-v0.1.1"), func(s string) { said = append(said, s) }); err != nil {
		t.Fatal(err)
	}
	src := filepath.Join(home, "src")
	for _, p := range []string{"pyproject.toml", "scripts/setup.sh", "src/hiro/__init__.py"} {
		if _, err := os.Stat(filepath.Join(src, filepath.FromSlash(p))); err != nil {
			t.Fatalf("顶级目录没去掉或文件缺失：%v", err)
		}
	}
	if _, err := os.Stat(filepath.Join(src, "hiro-gakumas-hero-desktop-v0.1.1")); err == nil {
		t.Fatal("顶级目录还在")
	}
	if st, _ := os.Stat(filepath.Join(src, "scripts", "setup.sh")); st.Mode()&0o100 == 0 && !strings.Contains(os.Getenv("OS"), "Windows") {
		t.Fatalf("可执行位丢了：%v", st.Mode())
	}
	info := readSourceInfo(t, src)
	if info["source"] != "tag" || info["ref"] != "desktop-v0.1.1" || info["commit"] != fakeCommit || info["downloaded_at"] == "" {
		t.Fatalf("info %v", info)
	}
	if _, err := time.Parse(time.RFC3339, info["downloaded_at"]); err != nil {
		t.Fatal(err)
	}
	if gh.hitList() != "/archive/refs/tags/desktop-v0.1.1.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
	if strings.Contains(strings.Join(said, "\n"), "最新开发版") {
		t.Fatalf("按标签下载成功不该提示开发版：%v", said)
	}
	noLeftovers(t, home)
}

func TestFetchSourceFallsBackToMainWhenTheTagIs404(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/heads/main.tar.gz": makeArchive(t, "hiro-gakumas-hero-main", fakeCommit, repoFiles...),
	})
	home := t.TempDir()
	var said []string
	if err := FetchSource(context.Background(), spec(gh, home, "desktop-v9.9.9"), func(s string) { said = append(said, s) }); err != nil {
		t.Fatal(err)
	}
	if gh.hitList() != "/archive/refs/tags/desktop-v9.9.9.tar.gz,/archive/refs/heads/main.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
	if !strings.Contains(strings.Join(said, "\n"), "用的是最新开发版代码") {
		t.Fatalf("进度里要说明用的是开发版：%v", said)
	}
	info := readSourceInfo(t, filepath.Join(home, "src"))
	if info["source"] != "main" || info["ref"] != "main" || info["commit"] != fakeCommit {
		t.Fatalf("info %v", info)
	}
	noLeftovers(t, home)
}

func TestFetchSourceWithoutATagGoesStraightToMainAndRecordsOnlyTimeWhenNoCommit(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/heads/main.tar.gz": makeArchive(t, "hiro-gakumas-hero-main", "", repoFiles...),
	})
	home := t.TempDir()
	if err := FetchSource(context.Background(), spec(gh, home, ""), func(string) {}); err != nil {
		t.Fatal(err)
	}
	if gh.hitList() != "/archive/refs/heads/main.tar.gz" {
		t.Fatalf("hits %s", gh.hitList())
	}
	info := readSourceInfo(t, filepath.Join(home, "src"))
	if _, has := info["commit"]; has || info["source"] != "main" || info["downloaded_at"] == "" {
		t.Fatalf("info %v", info)
	}
}

func TestFetchSourceReadsTheCommitFromTheTopDirNameToo(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{
		"/archive/refs/tags/desktop-v1.0.0.tar.gz": makeArchive(t, "hiro-gakumas-hero-"+fakeCommit, "", repoFiles...),
	})
	home := t.TempDir()
	if err := FetchSource(context.Background(), spec(gh, home, "desktop-v1.0.0"), func(string) {}); err != nil {
		t.Fatal(err)
	}
	if info := readSourceInfo(t, filepath.Join(home, "src")); info["commit"] != fakeCommit {
		t.Fatalf("info %v", info)
	}
}

func TestFetchSourceLeavesNoHalfBakedSrcOnFailure(t *testing.T) {
	good := makeArchive(t, "hiro-gakumas-hero-main", "", repoFiles...)
	cases := map[string][]byte{
		"截断的压缩包":   good[:len(good)/2],
		"不是压缩包":    []byte("<html>not found</html>"),
		"没有安装脚本的包": makeArchive(t, "x-main", "", tarFile{name: "README.md", body: "hi"}),
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			gh := newFakeGitHub(t, map[string][]byte{"/archive/refs/heads/main.tar.gz": body})
			home := t.TempDir()
			if err := FetchSource(context.Background(), spec(gh, home, ""), func(string) {}); err == nil {
				t.Fatal("应该失败")
			}
			if _, err := os.Stat(filepath.Join(home, "src")); err == nil {
				t.Fatal("失败后不该留下 src")
			}
			noLeftovers(t, home)
		})
	}
}

func TestFetchSourceRefusesPathsThatEscapeTheDirectory(t *testing.T) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	_ = tw.WriteHeader(&tar.Header{Typeflag: tar.TypeReg, Name: "top/../../evil.txt", Mode: 0o644, Size: 1})
	_, _ = tw.Write([]byte("x"))
	_ = tw.Close()
	_ = gz.Close()
	gh := newFakeGitHub(t, map[string][]byte{"/archive/refs/heads/main.tar.gz": buf.Bytes()})
	root := t.TempDir()
	home := filepath.Join(root, "home")
	_ = os.MkdirAll(home, 0o755)
	if err := FetchSource(context.Background(), spec(gh, home, ""), func(string) {}); err == nil {
		t.Fatal("路径逃出目录的包应该被拒绝")
	}
	if _, err := os.Stat(filepath.Join(root, "evil.txt")); err == nil {
		t.Fatal("文件写到了目录外")
	}
}

func TestFetchSourceRetriesServerErrorsButNotA404(t *testing.T) {
	var mu sync.Mutex
	calls := 0
	good := makeArchive(t, "hiro-gakumas-hero-main", "", repoFiles...)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		calls++
		n := calls
		mu.Unlock()
		if n < 3 {
			http.Error(w, "boom", http.StatusBadGateway)
			return
		}
		_, _ = w.Write(good)
	}))
	defer srv.Close()
	home := t.TempDir()
	sp := SourceSpec{Repo: srv.URL, Dest: filepath.Join(home, "src"), Client: srv.Client(), Backoff: time.Millisecond}
	if err := FetchSource(context.Background(), sp, func(string) {}); err != nil {
		t.Fatalf("前两次 502、第三次成功应该装上：%v", err)
	}
	if calls != 3 {
		t.Fatalf("calls %d", calls)
	}
	// 一直 502：用完次数后报错，不留半截目录
	calls = -100
	home2 := t.TempDir()
	sp.Dest = filepath.Join(home2, "src")
	if err := FetchSource(context.Background(), sp, func(string) {}); err == nil {
		t.Fatal("一直 502 应该失败")
	}
	noLeftovers(t, home2)
}

func TestFetchSourceReplacesAnIncompleteSrc(t *testing.T) {
	gh := newFakeGitHub(t, map[string][]byte{"/archive/refs/heads/main.tar.gz": makeArchive(t, "hiro-gakumas-hero-main", "", repoFiles...)})
	home := t.TempDir()
	stale := filepath.Join(home, "src")
	_ = os.MkdirAll(filepath.Join(stale, ".git"), 0o755) // 旧版 git clone 中途失败留下的半截目录
	if err := FetchSource(context.Background(), spec(gh, home, ""), func(string) {}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(stale, ".git")); err == nil {
		t.Fatal("半截目录没清掉")
	}
	if _, err := os.Stat(filepath.Join(stale, "scripts", "setup.sh")); err != nil {
		t.Fatal(err)
	}
}

func TestSourceArchiveURLIsOneFunction(t *testing.T) {
	if got := sourceArchiveURL("https://github.com/matto49/hiro-gakumas-hero", sourceRef{Kind: "tag", Name: "desktop-v0.1.0"}); got != "https://github.com/matto49/hiro-gakumas-hero/archive/refs/tags/desktop-v0.1.0.tar.gz" {
		t.Fatal(got)
	}
	if got := sourceArchiveURL("https://github.com/matto49/hiro-gakumas-hero/", sourceRef{Kind: "main", Name: "main"}); got != "https://github.com/matto49/hiro-gakumas-hero/archive/refs/heads/main.tar.gz" {
		t.Fatal(got)
	}
}
