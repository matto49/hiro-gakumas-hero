// fetchuv 下载要随安装包带的 uv（装 Python 用）到 resources/<平台>/bin/。构建安装包前在 app/ 里跑一次：
//
//	go run ./scripts/fetchuv [darwin-arm64 darwin-amd64 windows-amd64]
//
// 不带参数就三个平台都下。版本用 UV_VERSION 换。
package main

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
)

type target struct{ asset, bin string }

var targets = map[string]target{
	"darwin-arm64":  {"uv-aarch64-apple-darwin.tar.gz", "uv"},
	"darwin-amd64":  {"uv-x86_64-apple-darwin.tar.gz", "uv"},
	"windows-amd64": {"uv-x86_64-pc-windows-msvc.zip", "uv.exe"},
}

func main() {
	version := os.Getenv("UV_VERSION")
	if version == "" {
		version = "0.13.8"
	}
	want := os.Args[1:]
	if len(want) == 0 {
		want = []string{"darwin-arm64", "darwin-amd64", "windows-amd64"}
	}
	for _, plat := range want {
		for _, p := range strings.Fields(plat) {
			if err := fetch(version, p); err != nil {
				fmt.Fprintln(os.Stderr, p+":", err)
				os.Exit(1)
			}
		}
	}
}

func fetch(version, plat string) error {
	t, ok := targets[plat]
	if !ok {
		return fmt.Errorf("不认识的平台 %s", plat)
	}
	dest := filepath.Join("resources", plat, "bin", t.bin)
	if _, err := os.Stat(dest); err == nil {
		fmt.Printf("%s: 已有 %s\n", plat, dest)
		return nil
	}
	url := "https://github.com/astral-sh/uv/releases/download/" + version + "/" + t.asset
	fmt.Printf("%s: 下载 %s\n", plat, url)
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("%s: %s", url, resp.Status)
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	bin, err := extract(data, t)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	return os.WriteFile(dest, bin, 0o755)
}

// extract 从压缩包里取出名叫 t.bin 的文件。
func extract(data []byte, t target) ([]byte, error) {
	if strings.HasSuffix(t.asset, ".zip") {
		zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			return nil, err
		}
		for _, f := range zr.File {
			if path.Base(f.Name) == t.bin && !f.FileInfo().IsDir() {
				rc, err := f.Open()
				if err != nil {
					return nil, err
				}
				defer rc.Close()
				return io.ReadAll(rc)
			}
		}
		return nil, fmt.Errorf("压缩包里没有 %s", t.bin)
	}
	gz, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			return nil, fmt.Errorf("压缩包里没有 %s", t.bin)
		}
		if err != nil {
			return nil, err
		}
		if path.Base(h.Name) == t.bin && h.Typeflag == tar.TypeReg {
			return io.ReadAll(tr)
		}
	}
}
