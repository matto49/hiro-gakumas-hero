package main

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"testing"
)

func TestExtractFindsBinaryInZipAndTarGz(t *testing.T) {
	var zb bytes.Buffer
	zw := zip.NewWriter(&zb)
	w, _ := zw.Create("uv-x86_64-pc-windows-msvc/uv.exe")
	_, _ = w.Write([]byte("EXE"))
	_ = zw.Close()
	if b, err := extract(zb.Bytes(), target{"x.zip", "uv.exe"}); err != nil || string(b) != "EXE" {
		t.Errorf("zip：%q %v", b, err)
	}
	var tb bytes.Buffer
	gz := gzip.NewWriter(&tb)
	tw := tar.NewWriter(gz)
	_ = tw.WriteHeader(&tar.Header{Name: "uv-aarch64-apple-darwin/uv", Mode: 0o755, Size: 3, Typeflag: tar.TypeReg})
	_, _ = tw.Write([]byte("BIN"))
	_ = tw.Close()
	_ = gz.Close()
	if b, err := extract(tb.Bytes(), target{"x.tar.gz", "uv"}); err != nil || string(b) != "BIN" {
		t.Errorf("tar.gz：%q %v", b, err)
	}
}
