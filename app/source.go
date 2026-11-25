package main

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// 下载初星助手的 Python 源码（HIRO_HOME/src）：不用 git，普通玩家的电脑上没有它
// （Mac 没装开发者工具时 /usr/bin/git 只是个会弹「安装命令行开发者工具」的占位程序）。
// 按安装包自己的版本取 GitHub 标签 desktop-v<版本> 的源码压缩包（tar.gz，两个平台都用标准库解）；
// 标签不存在（开发版、版本还没发）时退回 main，并记下装的是哪一份（.hiro-source.json）。

// sourceInfoFile 记着 src 是哪一份源码下来的（版本页要显示）：{"source": "tag|main|ref", "ref", "commit"?, "url", "downloaded_at"}。
const sourceInfoFile = ".hiro-source.json"

// sourceRef 是要下载的那一份源码：Kind 为 tag（版本标签）、main（最新开发版）或 ref（HIRO_REPO_REF 指定的分支 / 标签）。
type sourceRef struct{ Kind, Name string }

// sourceArchiveURL 是源码压缩包的下载地址。下载地址集中在这一个函数，以后改成从 Release 资产下载只改这里。
func sourceArchiveURL(repo string, r sourceRef) string {
	repo = strings.TrimRight(repo, "/")
	switch r.Kind {
	case "tag":
		return repo + "/archive/refs/tags/" + r.Name + ".tar.gz"
	case "main":
		return repo + "/archive/refs/heads/main.tar.gz"
	}
	return repo + "/archive/" + r.Name + ".tar.gz"
}

// SourceSpec 说明要下什么、放到哪。
type SourceSpec struct {
	Repo    string        // 仓库地址，如 https://github.com/matto49/hiro-gakumas-hero
	Tag     string        // 本版本对应的标签（desktop-v<版本>）；空 = 没有对应版本（开发版），直接用 main
	Ref     string        // 显式指定的分支 / 标签（HIRO_REPO_REF）：只下它，下不到就报错，不退回 main
	Dest    string        // 解压到哪（HIRO_HOME/src）
	Client  *http.Client  // nil：用带超时的默认客户端
	Tries   int           // 每份压缩包最多试几次，0 = 3
	Backoff time.Duration // 重试前等多久，0 = 2 秒（第 n 次等 n 倍）
	Now     func() time.Time
}

var errSourceNotFound = errors.New("没有这份源码包（404）")

// FetchSource 下载并解压源码到 spec.Dest：先下到临时文件、解压到临时目录，成功后再改名成 Dest，中途失败不留半截目录。
func FetchSource(ctx context.Context, spec SourceSpec, say func(string)) error {
	home := filepath.Dir(spec.Dest)
	if err := os.MkdirAll(home, 0o755); err != nil {
		return err
	}
	var refs []sourceRef
	switch {
	case spec.Ref == "main":
		refs = []sourceRef{{Kind: "main", Name: "main"}}
	case spec.Ref != "":
		refs = []sourceRef{{Kind: "ref", Name: spec.Ref}}
	case spec.Tag != "":
		refs = []sourceRef{{Kind: "tag", Name: spec.Tag}, {Kind: "main", Name: "main"}}
	default:
		refs = []sourceRef{{Kind: "main", Name: "main"}}
	}
	for i, r := range refs {
		url := sourceArchiveURL(spec.Repo, r)
		err := fetchOne(ctx, spec, r, url, home, say)
		if err == nil {
			return nil
		}
		if errors.Is(err, errSourceNotFound) && i+1 < len(refs) {
			say("没有 " + r.Name + " 对应的源码包（开发版或这个版本还没发布），用的是最新开发版代码（main）")
			continue
		}
		return fmt.Errorf("下载 %s 失败：%w", url, err)
	}
	return errors.New("没有可下载的源码")
}

func fetchOne(ctx context.Context, spec SourceSpec, r sourceRef, url, home string, say func(string)) error {
	tmpFile, err := os.CreateTemp(home, ".src-download-*.tar.gz")
	if err != nil {
		return err
	}
	defer os.Remove(tmpFile.Name())
	defer tmpFile.Close()
	if err := download(ctx, spec, url, tmpFile, say); err != nil {
		return err
	}
	tmpDir, err := os.MkdirTemp(home, ".src-extract-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmpDir)
	say("解压源码…")
	if _, err := tmpFile.Seek(0, io.SeekStart); err != nil {
		return err
	}
	commit, err := extractTarGz(tmpFile, tmpDir)
	if err != nil {
		return err
	}
	if _, err := os.Stat(filepath.Join(tmpDir, "scripts", "setup.sh")); err != nil {
		return errors.New("压缩包里没有 scripts/setup.sh，不像是初星助手的源码")
	}
	now := time.Now
	if spec.Now != nil {
		now = spec.Now
	}
	info := map[string]string{"source": r.Kind, "ref": r.Name, "url": url, "downloaded_at": now().Format(time.RFC3339)}
	if commit != "" {
		info["commit"] = commit
	}
	b, _ := json.MarshalIndent(info, "", "  ")
	if err := os.WriteFile(filepath.Join(tmpDir, sourceInfoFile), append(b, '\n'), 0o644); err != nil {
		return err
	}
	// 目标已存在说明是旧版 git clone 中途失败留下的半截目录（PlanBootstrap 只在源码不完整时才走到这里）
	if _, err := os.Stat(spec.Dest); err == nil {
		say("清掉上次没装完的 src 目录")
		if err := os.RemoveAll(spec.Dest); err != nil {
			return err
		}
	}
	return os.Rename(tmpDir, spec.Dest)
}

func newSourceClient() *http.Client {
	return &http.Client{Transport: &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSHandshakeTimeout:   15 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
	}}
}

// download 把 url 下到 w：连不上、5xx、中途断开都重试；404 立刻返回 errSourceNotFound；60 秒收不到数据算卡住。
func download(ctx context.Context, spec SourceSpec, url string, w interface {
	io.Writer
	io.Seeker
	Truncate(int64) error
}, say func(string)) error {
	client := spec.Client
	if client == nil {
		client = newSourceClient()
	}
	tries, backoff := spec.Tries, spec.Backoff
	if tries <= 0 {
		tries = 3
	}
	if backoff <= 0 {
		backoff = 2 * time.Second
	}
	var last error
	for n := 1; n <= tries; n++ {
		if n > 1 {
			say(fmt.Sprintf("下载没成功（%v），%d 秒后重试（%d/%d）", last, int(backoff.Seconds()*float64(n-1)), n, tries))
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(backoff * time.Duration(n-1)):
			}
		}
		err := downloadOnce(ctx, client, url, w, say)
		if err == nil {
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		var fatal *fatalStatus
		if errors.Is(err, errSourceNotFound) || errors.As(err, &fatal) {
			return err
		}
		last = err
	}
	return last
}

type fatalStatus struct{ code int }

func (f *fatalStatus) Error() string { return fmt.Sprintf("服务器返回 %d", f.code) }

const stallTimeout = 60 * time.Second

func downloadOnce(ctx context.Context, client *http.Client, url string, w interface {
	io.Writer
	io.Seeker
	Truncate(int64) error
}, say func(string)) error {
	if err := w.Truncate(0); err != nil {
		return err
	}
	if _, err := w.Seek(0, io.SeekStart); err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stall := time.AfterFunc(stallTimeout, cancel)
	defer stall.Stop()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "hiro-console")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound:
		return errSourceNotFound
	case resp.StatusCode >= 500 || resp.StatusCode == http.StatusTooManyRequests:
		return fmt.Errorf("服务器返回 %d", resp.StatusCode)
	case resp.StatusCode != http.StatusOK:
		return &fatalStatus{resp.StatusCode}
	}
	buf := make([]byte, 64*1024)
	var total, reported int64
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			stall.Reset(stallTimeout)
			if _, err := w.Write(buf[:n]); err != nil {
				return err
			}
			total += int64(n)
			if total-reported >= 8<<20 {
				reported = total
				say(fmt.Sprintf("已下载 %d MB", total>>20))
			}
		}
		if rerr == io.EOF {
			return nil
		}
		if rerr != nil {
			return rerr
		}
	}
}

var commitRE = regexp.MustCompile(`^[0-9a-f]{40}$`)

// extractTarGz 解到 dest，去掉压缩包里唯一的顶级目录；返回能拿到的提交号（GitHub 把它放在 pax 全局头的 comment，
// 按提交下载时顶级目录名也以它结尾），拿不到返回空。路径逃出 dest 的条目直接报错；符号链接等特殊条目跳过（仓库里没有）。
func extractTarGz(r io.Reader, dest string) (string, error) {
	gz, err := gzip.NewReader(r)
	if err != nil {
		return "", fmt.Errorf("不是有效的压缩包：%w", err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var commit string
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", fmt.Errorf("压缩包损坏：%w", err)
		}
		if h.Typeflag == tar.TypeXGlobalHeader {
			if c := h.PAXRecords["comment"]; commitRE.MatchString(c) {
				commit = c
			}
			continue
		}
		name := strings.TrimPrefix(h.Name, "./")
		top, rel, _ := strings.Cut(name, "/")
		if commit == "" {
			if i := strings.LastIndex(top, "-"); i >= 0 && commitRE.MatchString(top[i+1:]) {
				commit = top[i+1:]
			}
		}
		rel = strings.TrimSuffix(rel, "/")
		if rel == "" {
			continue // 顶级目录本身
		}
		local := filepath.FromSlash(rel)
		if !filepath.IsLocal(local) {
			return "", fmt.Errorf("压缩包里有越界的路径：%s", h.Name)
		}
		target := filepath.Join(dest, local)
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, 0o755); err != nil {
				return "", err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return "", err
			}
			perm := os.FileMode(0o644)
			if h.Mode&0o111 != 0 {
				perm = 0o755
			}
			f, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, perm)
			if err != nil {
				return "", err
			}
			_, cerr := io.Copy(f, tr)
			if err := f.Close(); cerr == nil {
				cerr = err
			}
			if cerr != nil {
				return "", fmt.Errorf("解压 %s 失败：%w", rel, cerr)
			}
		}
	}
	return commit, nil
}
