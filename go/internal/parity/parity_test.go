// Package parity 是 Go / Python 对照测试：同一个 HIRO_HOME 准备法、同一组命令，两边输出的 JSON 要一致
// （比语义、不比空格；去掉编号、时间这类每次都变的字段）。本机有 Python 环境时跑：
//
//	HIRO_PARITY_PYTHON=<python> [HIRO_PARITY_SRC=<仓库>/src] go test -run Parity -v ./internal/parity/
//
// 没设 HIRO_PARITY_PYTHON 时用 pybridge 找到的 Python（HIRO_PYTHON → 仓库 .venv）；都没有就跳过。
package parity

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/hiro"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

var data = filepath.Join("..", "..", "..", "tests", "cli", "data")

type side struct {
	name string
	home string
	run  func(t *testing.T, home string, argv ...string) (int, map[string]any)
}

func python(t *testing.T) (string, string) {
	t.Helper()
	py := os.Getenv("HIRO_PARITY_PYTHON")
	if py == "" {
		p, err := pybridge.Python()
		if err != nil {
			t.Skip("没有 Python 环境（设 HIRO_PARITY_PYTHON）")
		}
		py = p
	}
	src := os.Getenv("HIRO_PARITY_SRC")
	if src == "" {
		repo, err := pybridge.Repo()
		if err != nil {
			t.Skip("找不到仓库（设 HIRO_PARITY_SRC）")
		}
		src = filepath.Join(repo, "src")
	}
	return py, src
}

func lastEnvelope(t *testing.T, out []byte) map[string]any {
	t.Helper()
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	var env map[string]any
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &env); err != nil {
		t.Fatalf("不是 JSON：%q", out)
	}
	return env
}

func pySide(t *testing.T) side {
	py, src := python(t)
	return side{name: "python", run: func(t *testing.T, home string, argv ...string) (int, map[string]any) {
		t.Helper()
		cmd := exec.Command(py, append([]string{"-m", "hiro.cli.main"}, argv...)...)
		cmd.Env = append(os.Environ(), "HIRO_HOME="+home, "PYTHONPATH="+src, "HIRO_SECRET_BACKEND=memory", "PYTHONUTF8=1")
		out, err := cmd.Output()
		code := 0
		if ee, ok := err.(*exec.ExitError); ok { //nolint:errorlint // 只看退出码
			code = ee.ExitCode()
		} else if err != nil {
			t.Fatal(err)
		}
		return code, lastEnvelope(t, out)
	}}
}

func goSide() side {
	return side{name: "go", run: func(t *testing.T, home string, argv ...string) (int, map[string]any) {
		t.Helper()
		t.Setenv("HIRO_HOME", home)
		t.Setenv("HIRO_SECRET_BACKEND", "memory")
		var out bytes.Buffer
		code := hiro.Registry().Main(argv, &cli.Ctx{Stdout: &out, Stdin: strings.NewReader("")})
		return code, lastEnvelope(t, out.Bytes())
	}}
}

// volatile 是每次都变的字段：比较前去掉。
var volatile = map[string]bool{"id": true, "expires_at": true, "created_at": true, "t": true}

func normalize(v any, home string) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, vv := range x {
			if !volatile[k] {
				out[k] = normalize(vv, home)
			}
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, vv := range x {
			out[i] = normalize(vv, home)
		}
		return out
	case string:
		return pendingID.ReplaceAllString(strings.ReplaceAll(x, home, "<HOME>"), "<ID>")
	}
	return v
}

var pendingID = regexp.MustCompile(`\b[0-9a-f]{12}\b`)

// osErrorCodes：message 里带系统的报错原文（Python 的 [Errno 2]、Go 的 open …），只比「：」前面那段。
var osErrorCodes = map[string]bool{"bad_file": true, "network": true}

func head(v any) string { s, _ := v.(string); h, _, _ := strings.Cut(s, "："); return h }

// argparseCodes：这些错误的 message 是参数解析器写的（Python 英文、Go 中文），只比 code 和退出码。
var argparseCodes = map[string]bool{"bad_args": true}

type step struct {
	argv  []string
	setup bool // 只准备数据，不比较
	drop  []string
}

func compare(t *testing.T, py, gs side, steps []step) {
	t.Helper()
	py.home, gs.home = t.TempDir(), t.TempDir()
	for _, s := range steps {
		pc, pe := py.run(t, py.home, s.argv...)
		gc, ge := gs.run(t, gs.home, s.argv...)
		if s.setup {
			if pc != gc {
				t.Fatalf("准备 %v：退出码 python %d / go %d", s.argv, pc, gc)
			}
			continue
		}
		label := strings.Join(s.argv, " ")
		if pc != gc {
			t.Fatalf("%s：退出码 python %d / go %d\npython %v\ngo     %v", label, pc, gc, pe, ge)
		}
		if pe["ok"] == false {
			perr, gerr := pe["error"].(map[string]any), ge["error"].(map[string]any)
			if perr["code"] != gerr["code"] {
				t.Fatalf("%s：错误码 python %v / go %v", label, perr["code"], gerr["code"])
			}
			code, _ := perr["code"].(string)
			msgDiffers := perr["message"] != gerr["message"]
			if osErrorCodes[code] {
				msgDiffers = head(perr["message"]) != head(gerr["message"])
			}
			if !argparseCodes[code] && (msgDiffers || perr["hint"] != gerr["hint"]) {
				t.Fatalf("%s：错误说明不一样\npython %v\ngo     %v", label, perr, gerr)
			}
			continue
		}
		pn, gn := normalize(pe, py.home), normalize(ge, gs.home)
		for _, k := range s.drop {
			delete(pn.(map[string]any)["data"].(map[string]any), k)
			delete(gn.(map[string]any)["data"].(map[string]any), k)
		}
		if !reflect.DeepEqual(pn, gn) {
			pj, _ := json.MarshalIndent(pn, "", " ")
			gj, _ := json.MarshalIndent(gn, "", " ")
			t.Fatalf("%s：输出不一样\npython %s\ngo     %s", label, pj, gj)
		}
	}
}

func file(t *testing.T, name, content string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

func abs(t *testing.T, p string) string {
	t.Helper()
	a, err := filepath.Abs(p)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func TestParityTools(t *testing.T) {
	compare(t, pySide(t), goSide(), []step{{argv: []string{"tools"}}})
}

func TestParityInfo(t *testing.T) {
	sup, mem := abs(t, filepath.Join(data, "support-inventory.json")), abs(t, filepath.Join(data, "memory-inventory.json"))
	gs := file(t, "gs.json", `{"items": {"未読早送り": "ON", "支援事件剧情跳过": "OFF", "开始说明剧情跳过": null}, "verdict": "1 项 OFF", "ok": false}`)
	acc := file(t, "acc.json", `{"scanned_at": "2026-11-17 18:00:00", "plv": 65, "ap": 85, "ap_max": 100, "jewel": 15220}`)
	compare(t, pySide(t), goSide(), []step{
		{argv: []string{"info", "summary"}},
		{argv: []string{"info", "supports"}},
		{argv: []string{"info", "idols"}},
		{argv: []string{"info", "affection"}},
		{argv: []string{"info", "game-settings"}},
		{argv: []string{"info", "import", "--kind", "supports", sup}},
		{argv: []string{"info", "import", "--kind", "memories", mem}},
		{argv: []string{"info", "import", "--kind", "game_settings", gs}},
		{argv: []string{"info", "import", "--kind", "account", acc}},
		{argv: []string{"info", "summary"}},
		{argv: []string{"info", "supports"}},
		{argv: []string{"info", "supports", "--rarity", "SSR"}},
		{argv: []string{"info", "supports", "--name", "美鈴", "--attr", "Vi"}},
		{argv: []string{"info", "memories"}},
		{argv: []string{"info", "memories", "--timing", "start"}},
		{argv: []string{"info", "game-settings"}},
		{argv: []string{"info", "supports", "--rarity", "UR"}},
		{argv: []string{"info", "import", "--kind", "supports", "/nonexistent.json"}},
	})
}

func TestParityPendingPlanRunSecret(t *testing.T) {
	compare(t, pySide(t), goSide(), []step{
		{argv: []string{"pending", "list"}},
		{argv: []string{"plan", "show"}},
		{argv: []string{"plan", "formation", "--idol", "i_card-skin-shro-3-007"}},
		{argv: []string{"catalog", "search", "エウレカ"}},
		{argv: []string{"catalog", "get", "x"}},
		{argv: []string{"run", "start", "hajime"}},
		{argv: []string{"run", "start", "nia", "--idol", "x", "--difficulty", "regular"}},
		{argv: []string{"run", "start", "daily", "--items", "presents,nonsense"}},
		{argv: []string{"run", "start", "write-formation"}},
		{argv: []string{"run", "start", "hajime", "--idol", "i_card-skin-shro-3-007", "--difficulty", "master", "--support-set", "2"}},
		{argv: []string{"run", "start", "daily", "--items", "presents,contest"}},
		{argv: []string{"run", "start", "hif", "--mode", "main"}},
		{argv: []string{"run", "start", "write-formation", "--dry-run"}},
		{argv: []string{"run", "report", "--job", "nope"}},
		{argv: []string{"run", "report", "--job", "../x"}},
		{argv: []string{"secret", "list"}},
		{argv: []string{"secret", "copy", "game-password"}},
		{argv: []string{"secret", "set", "../x"}},
		{argv: []string{"no-such-thing"}},
		{argv: []string{"confirm", "../../etc/passwd"}},
		{argv: []string{"cancel", "0123456789ab"}},
	})
}

func TestParityPlanShow(t *testing.T) {
	py, gs := pySide(t), goSide()
	py.home, gs.home = t.TempDir(), t.TempDir()
	plan := `{"version": 1, "created": "2026-11-17 16:00:00",
 "idol": {"idol_card_id": "i_card-shro-3-007", "name": "Campus mode!!", "plan": "ProducePlanType_Plan2"},
 "profile": "logic", "main_attr": "Da", "notes": ["4 张集中在 Da"],
 "support": {"set": 4, "value": 199, "counts": {"Vo": 0, "Da": 4, "Vi": 2},
   "own": [{"support_card_id": "s1", "name": "そろそろ焼けたかな？", "attr": "Da", "level": 60, "value": 20, "why": ["a", "b", "c", "d", "e"]}],
   "rental_rule": {"attr": "Vi"}},
 "memory": {"set": 5, "cards": ["p1"], "chosen": [{"id": "x", "card_id": "p1", "name": "ワクワク", "timing": "start", "slot": "A", "role": "核心"}]}}`
	for _, h := range []string{py.home, gs.home} {
		if err := os.WriteFile(filepath.Join(h, "formation-plan.json"), []byte(plan), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	_, pe := py.run(t, py.home, "plan", "show")
	_, ge := gs.run(t, gs.home, "plan", "show")
	if pn, gn := normalize(pe, py.home), normalize(ge, gs.home); !reflect.DeepEqual(pn, gn) {
		t.Fatalf("plan show\npython %v\ngo     %v", pn, gn)
	}
}

func TestParityDoctor(t *testing.T) {
	py, gs := pySide(t), goSide()
	py.home, gs.home = t.TempDir(), t.TempDir()
	_, pe := py.run(t, py.home, "doctor")
	_, ge := gs.run(t, gs.home, "doctor")
	items := func(env map[string]any) map[string]map[string]any {
		out := map[string]map[string]any{}
		for _, v := range env["data"].(map[string]any)["items"].([]any) {
			o := v.(map[string]any)
			out[o["id"].(string)] = o
		}
		return out
	}
	pi, gi := items(pe), items(ge)
	for id, p := range pi {
		if id == "python" || id == "platform" { // 和本机环境有关
			continue
		}
		if !reflect.DeepEqual(p, gi[id]) {
			t.Fatalf("doctor %s\npython %v\ngo     %v", id, p, gi[id])
		}
	}
	if len(pi) != len(gi) {
		t.Fatalf("items %d vs %d", len(pi), len(gi))
	}
}
