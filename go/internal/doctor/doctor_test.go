package doctor

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/info"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

func setup(t *testing.T, pythonScript string) (*cli.Registry, string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("假 python 是 shell 脚本")
	}
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	t.Setenv("HIRO_DEVICE", "")
	py := filepath.Join(t.TempDir(), "python")
	if err := os.WriteFile(py, []byte("#!/bin/sh\n"+pythonScript), 0o700); err != nil { //nolint:gosec // 假 python
		t.Fatal(err)
	}
	t.Setenv("HIRO_PYTHON", py)
	r := cli.New()
	Register(r)
	info.Register(r)
	return r, h
}

func items(t *testing.T, r *cli.Registry) (map[string]*jsonx.Object, []string, *jsonx.Object) {
	t.Helper()
	var out bytes.Buffer
	if code := r.Main([]string{"doctor"}, &cli.Ctx{Stdout: &out}); code != 0 {
		t.Fatalf("code %d %s", code, out.String())
	}
	env, _ := jsonx.DecodeObject(bytes.TrimSpace(out.Bytes()))
	d := jsonx.Obj(env, "data")
	m := map[string]*jsonx.Object{}
	var order []string
	for _, v := range jsonx.List(d, "items") {
		o := v.(*jsonx.Object)
		m[jsonx.Str(o, "id")] = o
		order = append(order, jsonx.Str(o, "id"))
	}
	return m, order, d
}

const okPython = "echo 3.10.14\n"

func TestDoctorOnFreshHome(t *testing.T) {
	r, _ := setup(t, okPython)
	it, order, d := items(t, r)
	if strings.Join(order, ",") != "python,platform,sound,game_settings,model,secrets,catalog,scan" {
		t.Fatalf("%v", order)
	}
	if v, _ := it["python"].Get("ok"); v != true || jsonx.Str(it["python"], "detail") != "Python 3.10.14，依赖齐全" {
		t.Fatalf("python %v", it["python"])
	}
	if v, _ := it["catalog"].Get("ok"); v != false || !strings.Contains(jsonx.Str(it["catalog"], "next"), "hiro catalog sync") {
		t.Fatalf("catalog %v", it["catalog"])
	}
	if v, _ := it["scan"].Get("ok"); v != false || !strings.Contains(jsonx.Str(it["scan"], "detail"), "扫描") {
		t.Fatalf("scan %v", it["scan"])
	}
	if !jsonx.IsNull(it["sound"], "ok") {
		t.Fatal("sound needs the user")
	}
	if v, _ := it["secrets"].Get("optional"); v != true {
		t.Fatal("secrets optional")
	}
	for _, o := range it {
		if jsonx.Str(o, "title") == "" || jsonx.Str(o, "detail") == "" {
			t.Fatalf("empty %v", o)
		}
		if strings.Join(o.Keys(), ",") != "id,title,ok,detail,next,optional" {
			t.Fatalf("keys %v", o.Keys())
		}
	}
	if v, _ := d.Get("ready"); v != false {
		t.Fatal("not ready")
	}
}

func TestDoctorSeesCatalogAndScans(t *testing.T) {
	r, h := setup(t, okPython)
	if err := os.MkdirAll(filepath.Join(h, "catalog"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(h, "catalog", "catalog.ja.json"), []byte(`{"cards": {"a": {}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	r.Main([]string{"info", "import", "--kind", "supports", filepath.Join("..", "..", "..", "tests", "cli", "data", "support-inventory.json")}, &cli.Ctx{Stdout: &out})
	it, _, _ := items(t, r)
	if v, _ := it["catalog"].Get("ok"); v != true || jsonx.Str(it["catalog"], "detail") != "已下载（1 张技能卡）" {
		t.Fatalf("%v", it["catalog"])
	}
	if v, _ := it["scan"].Get("ok"); v != true || !strings.Contains(jsonx.Str(it["scan"], "detail"), "支援卡") {
		t.Fatalf("%v", it["scan"])
	}
}

func TestDoctorModel(t *testing.T) {
	r, h := setup(t, okPython)
	it, _, _ := items(t, r)
	if v, _ := it["model"].Get("ok"); v != false || !strings.Contains(jsonx.Str(it["model"], "detail"), "DeepSeek") || !strings.Contains(jsonx.Str(it["model"], "next"), "Key") {
		t.Fatalf("default %v", it["model"])
	}
	if err := os.WriteFile(filepath.Join(h, "settings.json"), []byte(`{"provider": "deepseek", "has_key": true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if it, _, _ = items(t, r); jsonx.Str(it["model"], "detail") != "已选 DeepSeek" {
		t.Fatalf("%v", it["model"])
	}
	if err := os.WriteFile(filepath.Join(h, "settings.json"), []byte(`{"provider": "none"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if it, _, _ = items(t, r); !strings.Contains(jsonx.Str(it["model"], "detail"), "不用模型") {
		t.Fatalf("%v", it["model"])
	}
}

func TestDoctorPythonMissingDependencies(t *testing.T) {
	r, _ := setup(t, "echo \"ModuleNotFoundError: No module named 'cv2'\" >&2; exit 1\n")
	it, _, _ := items(t, r)
	if v, _ := it["python"].Get("ok"); v != false || !strings.Contains(jsonx.Str(it["python"], "next"), "重新安装") {
		t.Fatalf("%v", it["python"])
	}
}
