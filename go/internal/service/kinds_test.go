package service

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

var rt = Runtime{Python: "/py", Repo: "/repo", Self: "/hiro"}

func params(kv ...any) *jsonx.Object {
	o := jsonx.NewObject()
	for i := 0; i+1 < len(kv); i += 2 {
		o.Set(kv[i].(string), kv[i+1])
	}
	return o
}

func argv(kind string, p *jsonx.Object, dir string) []string {
	return Kinds[kind].Argv(p, dir, rt)
}

func after(a []string, k string) string {
	for i, v := range a {
		if v == k && i+1 < len(a) {
			return a[i+1]
		}
	}
	return "<missing " + k + ">"
}

func TestKindOrderAndPausable(t *testing.T) {
	if strings.Join(KindNames, ",") != "selftest,hif,tower,hajime,nia,daily,write-formation,check-settings,fit-window,scan-account,scan-idols,scan-supports,scan-memories,scan-formations" {
		t.Fatalf("%v", KindNames)
	}
	for _, k := range []string{"hif", "tower", "hajime", "nia", "selftest"} {
		if !Kinds[k].Pausable {
			t.Fatalf("%s should be pausable", k)
		}
	}
	for _, k := range []string{"daily", "write-formation", "check-settings", "fit-window", "scan-account", "scan-idols", "scan-supports", "scan-memories", "scan-formations"} {
		if Kinds[k].Pausable {
			t.Fatalf("%s should not be pausable", k)
		}
	}
}

func TestHifArgv(t *testing.T) {
	a := argv("hif", params("command", "new", "mode", "qualify", "idol", "i_card-skin-shro-3-007", "main_attr", "Da", "no_llm", true), "/j/J1")
	if strings.Join(a[:4], " ") != "/py -m hiro.run_hif new" || after(a, "--main-attr") != "Da" || after(a, "--trace") != "J1" ||
		after(a, "--idol") != "i_card-skin-shro-3-007" || a[len(a)-1] != "--no-llm" {
		t.Fatalf("%v", a)
	}
	if a := argv("hif", params(), "/j/J1"); strings.Join(a, " ") != "/py -m hiro.run_hif new --mode qualify --trace J1" {
		t.Fatalf("defaults %v", a)
	}
}

func TestHajimeAndNiaArgv(t *testing.T) {
	d := filepath.Join("/j", "J1")
	a := argv("hajime", params("command", "new", "idol", "i_card-skin-shro-3-007", "difficulty", "pro", "support_set", 3, "memory_set", 7), d)
	if strings.Join(a[:4], " ") != "/py -m hiro.runtime.hajime new" {
		t.Fatalf("%v", a)
	}
	for k, v := range map[string]string{"--idol": "i_card-skin-shro-3-007", "--difficulty": "pro", "--support-set": "3", "--memory-set": "7", "--trace": "J1", "--out": filepath.Join(d, "frames")} {
		if after(a, k) != v {
			t.Fatalf("%s: %v", k, a)
		}
	}
	a = argv("nia", params("command", "resume", "idol", "i_card-skin-shro-3-007"), d)
	if strings.Join(a[:4], " ") != "/py -m hiro.runtime.nia_sample resume" || after(a, "--difficulty") != "pro" {
		t.Fatalf("%v", a)
	}
}

func TestDailyArgv(t *testing.T) {
	daily := filepath.Join("/repo", "tools", "daily", "run_daily.py")
	if a := argv("daily", params("items", "presents,mission_reward"), "/j/J1"); strings.Join(a, " ") != "/py "+daily+" --only presents,mission_reward --own" {
		t.Fatalf("%v", a)
	}
	if a := argv("daily", params("items", "contest"), "/j/J1"); strings.Join(a[2:], " ") != "--only contest" {
		t.Fatalf("%v", a)
	}
	if a := argv("daily", params(), "/j/J1"); len(a) != 2 {
		t.Fatalf("%v", a)
	}
}

func TestPrepJobsArgv(t *testing.T) {
	d := t.TempDir()
	a := argv("write-formation", params("scenario", "hajime", "dry_run", true, "what", "memory"), d)
	if !strings.HasSuffix(a[1], filepath.Join("tools", "write_formation.py")) || strings.Join(a[2:], " ") != "--mode hajime --what memory --dry-run" {
		t.Fatalf("%v", a)
	}
	if a := argv("write-formation", params(), d); strings.Join(a[2:], " ") != "--mode hif" {
		t.Fatalf("%v", a)
	}
	a = argv("check-settings", params(), d)
	if !strings.HasSuffix(a[1], filepath.Join("tools", "check_settings.py")) || strings.Join(a[2:], " ") != "--json "+filepath.Join(d, "out.json") {
		t.Fatalf("%v", a)
	}
	if Kinds["check-settings"].Output != "game_settings" || Kinds["scan-account"].Output != "account" ||
		Kinds["scan-supports"].Output != "supports" || Kinds["scan-memories"].Output != "memories" {
		t.Fatal("outputs")
	}
	if a := argv("fit-window", params(), d); strings.Join(a, " ") != "/hiro "+WinWindowCommand+" fit" {
		t.Fatalf("%v", a)
	}
	if a := argv("scan-account", params(), d); strings.Join(a[1:], " ") != "-m hiro.vision.home_values --live --out "+filepath.Join(d, "out.json") {
		t.Fatalf("%v", a)
	}
	if a := argv("scan-supports", params(), d); !strings.HasSuffix(a[1], "support_inventory.py") || strings.Join(a[2:], " ") != "--list --from-home --out "+filepath.Join(d, "out.json") {
		t.Fatalf("%v", a)
	}
	if a := argv("scan-memories", params(), d); strings.Join(a[1:], " ") != "-m hiro.memory_scan scan --from-home --out "+filepath.Join(d, "out.json") {
		t.Fatalf("%v", a)
	}
	if a := argv("tower", params("idol", "篠澤広", "no_llm", true), "/j/J1"); strings.Join(a, " ") != "/py -m hiro.runtime.tower_play --idol-name 篠澤広 --layers 1 --trace J1 --no-llm" {
		t.Fatalf("%v", a)
	}
	if a := argv("selftest", params("seconds", 2), "/j/J1"); strings.Join(a, " ") != "/hiro "+SelftestCommand+" 2 J1" {
		t.Fatalf("%v", a)
	}
}

func TestSummaries(t *testing.T) {
	t.Setenv("HIRO_HOME", t.TempDir())
	cases := map[string][2]any{
		"hif-new":      {"hif", params("idol", "i_card-skin-shro-3-007")},
		"hif-main":     {"hif", params("mode", "main")},
		"hif-resume":   {"hif", params("command", "resume")},
		"hajime":       {"hajime", params("difficulty", "pro")},
		"nia":          {"nia", params()},
		"daily-some":   {"daily", params("items", "presents,contest")},
		"wf-dry":       {"write-formation", params("dry_run", true)},
		"wf":           {"write-formation", params("scenario", "nia")},
		"tower":        {"tower", params("idol", "篠澤広", "layers", 3)},
		"scan-account": {"scan-account", params()},
	}
	want := map[string]string{
		"hif-new":      "开一局 H.I.F 选拔（i_card-skin-shro-3-007）",
		"hif-main":     "开一局 H.I.F 本战（会用掉一份选拔回忆）",
		"hif-resume":   "接着打进行中的 H.I.F 培育",
		"hajime":       "开一局「初」 PRO",
		"nia":          "开一局 N.I.A PRO",
		"daily-some":   "领日常奖励（礼物、竞赛）",
		"wf-dry":       "对照编成方案看 H.I.F 的编成差在哪几格（只看，不改）",
		"wf":           "把编成方案写进游戏（N.I.A 的支援卡 / 回忆编成）",
		"tower":        "爬偶像之路（篠澤広，3 层）",
		"scan-account": "读首页的 PLv / AP / ジュエル（只截图，不点）",
	}
	for name, c := range cases {
		if got := Kinds[c[0].(string)].Summary(c[1].(*jsonx.Object)); got != want[name] {
			t.Fatalf("%s: %q want %q", name, got, want[name])
		}
	}
	// 资料库里有这张卡就写卡名
	d := filepath.Join(os.Getenv("HIRO_HOME"), "catalog")
	if err := os.MkdirAll(d, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(d, "catalog.ja.json"), []byte(`{"idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広"}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"hif", "hajime", "nia"} {
		if s := Kinds[k].Summary(params("idol", "i_card-skin-shro-3-007")); !strings.Contains(s, "【Campus mode!!】篠澤広") || strings.Contains(s, "i_card") {
			t.Fatalf("%s: %q", k, s)
		}
	}
}
