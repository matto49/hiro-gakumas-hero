package plan

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

// 同 tests/cli/test_plan.py 的 CANNED。
const canned = `{"version": 1, "created": "2026-11-17 16:00:00",
 "idol": {"idol_card_id": "i_card-shro-3-007", "name": "Campus mode!!", "plan": "ProducePlanType_Plan2", "profile": "logic"},
 "profile": "logic", "main_attr": "Da", "notes": ["4 张集中在 Da"],
 "support": {"set": 4, "main_attr": "Da", "value": 199, "counts": {"Vo": 0, "Da": 4, "Vi": 2},
   "own": [{"support_card_id": "s_card-3-0067", "name": "そろそろ焼けたかな？", "attr": "Da", "level": 60, "value": 20, "why": ["事件 Da+20", "a", "b", "c", "d"]}],
   "rental": {"name": "あなたにも作ってあげる！", "value": 20, "attr": "Da"}, "rental_estimated": true},
 "memory": {"set": 5, "cards": ["p_card-01-ido-3_105"],
   "chosen": [{"id": "p13-16-2", "card_id": "p_card-01-ido-3_105", "name": "ワクワクが止まらない", "upgrade": 1, "timing": "start", "slot": "A", "role": "核心"}]}}`

func reg(t *testing.T) (*cli.Registry, string) {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HIRO_HOME", h)
	r := cli.New()
	Register(r)
	info.Register(r)
	return r, h
}

func run(t *testing.T, r *cli.Registry, argv ...string) (int, *jsonx.Object) {
	t.Helper()
	var out bytes.Buffer
	code := r.Main(argv, &cli.Ctx{Stdout: &out})
	env, err := jsonx.DecodeObject(bytes.TrimSpace(out.Bytes()))
	if err != nil {
		t.Fatalf("not json %q", out.String())
	}
	return code, env
}

func TestShowWithoutPlan(t *testing.T) {
	r, _ := reg(t)
	_, env := run(t, r, "plan", "show")
	if jsonx.Str(jsonx.Obj(env, "error"), "code") != "no_plan" {
		t.Fatalf("%v", env)
	}
}

func TestShowSummarizesThePlanFile(t *testing.T) {
	r, h := reg(t)
	if err := os.WriteFile(filepath.Join(h, "formation-plan.json"), []byte(canned), 0o600); err != nil {
		t.Fatal(err)
	}
	code, env := run(t, r, "plan", "show")
	s := jsonx.Obj(env, "data")
	if code != 0 || jsonx.Str(jsonx.Obj(s, "idol"), "name") != "Campus mode!!" || jsonx.Str(s, "main_attr") != "Da" {
		t.Fatalf("%d %v", code, env)
	}
	if !strings.HasSuffix(jsonx.Str(s, "path"), "formation-plan.json") {
		t.Fatalf("path %q", jsonx.Str(s, "path"))
	}
	own, _ := jsonx.Marshal(jsonx.List(jsonx.Obj(s, "support"), "own")[0])
	if string(own) != `{"id":"s_card-3-0067","name":"そろそろ焼けたかな？","attr":"Da","level":60,"value":20,"why":["事件 Da+20","a","b","c"]}` {
		t.Fatalf("own %s", own)
	}
	rental, _ := jsonx.Marshal(jsonx.Obj(jsonx.Obj(s, "support"), "rental"))
	if string(rental) != `{"name":"あなたにも作ってあげる！","attr":"Da","value":20,"estimated":true}` {
		t.Fatalf("rental %s", rental)
	}
	chosen, _ := jsonx.Marshal(jsonx.List(jsonx.Obj(s, "memory"), "chosen")[0])
	if string(chosen) != `{"id":"p13-16-2","card_id":"p_card-01-ido-3_105","name":"ワクワクが止まらない","upgrade":1,"timing":"start","slot":"A","role":"核心"}` {
		t.Fatalf("chosen %s", chosen)
	}
	if strings.Join(s.Keys(), ",") != "created,idol,main_attr,support,memory,notes,path" {
		t.Fatalf("keys %v", s.Keys())
	}
	if strings.Join(jsonx.Obj(s, "support").Keys(), ",") != "set,error,value,counts,main_attr_why,own,rental" {
		t.Fatalf("support keys %v", jsonx.Obj(s, "support").Keys())
	}
}

func TestSummarizeRentalRules(t *testing.T) {
	p, _ := jsonx.DecodeObject([]byte(`{"support": {"rental_rule": {"attr": "Vi"}}, "memory": {"chosen": [{"name": "x"}]}}`))
	s := Summarize(p)
	got, _ := jsonx.Marshal(jsonx.Obj(jsonx.Obj(s, "support"), "rental"))
	if string(got) != `{"name":null,"attr":"Vi","value":null,"estimated":false}` {
		t.Fatalf("rental by rule %s", got)
	}
	if n, _ := jsonx.Int(jsonx.List(jsonx.Obj(s, "memory"), "chosen")[0].(*jsonx.Object), "upgrade"); n != 0 {
		t.Fatal("upgrade defaults to 0")
	}
	p, _ = jsonx.DecodeObject([]byte(`{}`))
	if !jsonx.IsNull(jsonx.Obj(Summarize(p), "support"), "rental") {
		t.Fatal("no rental → null")
	}
}

func TestFormationWithoutInventorySaysScanFirst(t *testing.T) {
	r, _ := reg(t)
	_, env := run(t, r, "plan", "formation", "--idol", "i_card-skin-shro-3-007")
	e := jsonx.Obj(env, "error")
	if jsonx.Str(e, "code") != "no_inventory" || !strings.Contains(jsonx.Str(e, "hint"), "扫描") {
		t.Fatalf("%v", env)
	}
}

func TestFormationIsForwardedToPython(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("假 python 是 shell 脚本")
	}
	r, _ := reg(t)
	repo := t.TempDir()
	if err := os.MkdirAll(filepath.Join(repo, "src", "hiro"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "src", "hiro", "__init__.py"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	py := filepath.Join(t.TempDir(), "python")
	if err := os.WriteFile(py, []byte("#!/bin/sh\necho \"{\\\"ok\\\": true, \\\"data\\\": {\\\"argv\\\": \\\"$*\\\"}}\"\n"), 0o700); err != nil { //nolint:gosec // 假 python
		t.Fatal(err)
	}
	t.Setenv("HIRO_REPO", repo)
	t.Setenv("HIRO_PYTHON", py)
	run(t, r, "info", "import", "--kind", "supports", filepath.Join("..", "..", "..", "tests", "cli", "data", "support-inventory.json"))
	code, env := run(t, r, "plan", "formation", "--idol", "i_card-skin-shro-3-007", "--support-set", "4", "--fixed-main")
	if code != 0 || jsonx.Str(jsonx.Obj(env, "data"), "argv") != "-m hiro.cli.main plan formation --idol i_card-skin-shro-3-007 --support-set 4 --fixed-main" {
		t.Fatalf("%d %v", code, env)
	}
}
