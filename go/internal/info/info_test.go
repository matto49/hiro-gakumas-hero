package info

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// 夹具和 Python 测试共用（tests/cli/data）。
var dataDir = filepath.Join("..", "..", "..", "tests", "cli", "data")

func reg(t *testing.T) *cli.Registry {
	t.Helper()
	t.Setenv("HIRO_HOME", t.TempDir())
	r := cli.New()
	Register(r)
	return r
}

func run(t *testing.T, r *cli.Registry, argv ...string) (int, *jsonx.Object, string) {
	t.Helper()
	var out bytes.Buffer
	code := r.Main(argv, &cli.Ctx{Stdout: &out, Stdin: strings.NewReader("")})
	env, err := jsonx.DecodeObject(bytes.TrimSpace(out.Bytes()))
	if err != nil {
		t.Fatalf("not json: %q", out.String())
	}
	return code, jsonx.Obj(env, "data"), out.String()
}

func names(items []any, key string) []string {
	var out []string
	for _, it := range items {
		out = append(out, jsonx.Str(it.(*jsonx.Object), key))
	}
	return out
}

func TestInfoWithoutScanSaysSo(t *testing.T) {
	r := reg(t)
	code, d, _ := run(t, r, "info", "supports")
	if code != 0 || !jsonx.IsNull(d, "scanned_at") || len(jsonx.List(d, "items")) != 0 || !strings.Contains(jsonx.Str(d, "hint"), "扫描") {
		t.Fatalf("%d %v", code, d)
	}
	if strings.Join(d.Keys(), ",") != "kind,scanned_at,complete,items,hint" {
		t.Fatalf("keys %v", d.Keys())
	}
}

func TestImportSupportsThenFilterByRarity(t *testing.T) {
	r := reg(t)
	code, d, _ := run(t, r, "info", "import", "--kind", "supports", filepath.Join(dataDir, "support-inventory.json"))
	if n, _ := jsonx.Int(d, "count"); code != 0 || n != 4 {
		t.Fatalf("import %d %v", code, d)
	}
	_, d, _ = run(t, r, "info", "supports", "--rarity", "SSR")
	items := jsonx.List(d, "items")
	if len(items) != 3 || strings.Contains(strings.Join(names(items, "name"), ","), "テストSR") {
		t.Fatalf("items %v", names(items, "name"))
	}
	first := items[0].(*jsonx.Object)
	if lv, _ := jsonx.Int(first, "level"); jsonx.Str(first, "rarity") != "SSR" || lv != 60 || jsonx.Str(first, "attr") != "Vi" {
		t.Fatalf("first %v", first)
	}
	// 一行的字段和顺序同 Python normalize_supports
	if strings.Join(first.Keys(), ",") != "id,name,rarity,attr,type,level,breaks" {
		t.Fatalf("row keys %v", first.Keys())
	}
	_, d, _ = run(t, r, "info", "supports", "--name", "美鈴", "--attr", "Vi")
	if len(jsonx.List(d, "items")) != 1 {
		t.Fatalf("name+attr filter %v", names(jsonx.List(d, "items"), "name"))
	}
}

func TestImportMemoriesAndFilterByTiming(t *testing.T) {
	r := reg(t)
	run(t, r, "info", "import", "--kind", "memories", filepath.Join(dataDir, "memory-inventory.json"))
	_, d, _ := run(t, r, "info", "memories", "--timing", "start")
	if len(jsonx.List(d, "items")) != 1 {
		t.Fatalf("items %v", d)
	}
	if v, _ := d.Get("complete"); v != true {
		t.Fatalf("complete %v", v)
	}
	_, d, _ = run(t, r, "info", "memories")
	c := jsonx.Obj(jsonx.Obj(d, "counts"), "by_timing")
	if got, _ := jsonx.Marshal(c); string(got) != `{"mid":2,"start":1}` {
		t.Fatalf("by_timing %s", got)
	}
}

func TestSummaryCountsEachKind(t *testing.T) {
	r := reg(t)
	run(t, r, "info", "import", "--kind", "supports", filepath.Join(dataDir, "support-inventory.json"))
	_, d, _ := run(t, r, "info", "summary")
	kinds := jsonx.Obj(d, "kinds")
	if n, _ := jsonx.Int(jsonx.Obj(kinds, "supports"), "count"); n != 4 {
		t.Fatalf("supports %v", kinds)
	}
	if n, _ := jsonx.Int(jsonx.Obj(kinds, "idols"), "count"); n != 0 || !jsonx.IsNull(jsonx.Obj(kinds, "idols"), "scanned_at") {
		t.Fatalf("idols %v", kinds)
	}
	if strings.Join(kinds.Keys(), ",") != "account,idols,supports,memories,affection,game_settings,formations" || !jsonx.IsNull(d, "account") {
		t.Fatalf("summary %v", d)
	}
}

func TestImportKeepsTheScanTimeFromGenerated(t *testing.T) {
	r := reg(t)
	run(t, r, "info", "import", "--kind", "supports", filepath.Join(dataDir, "support-inventory.json"))
	_, d, _ := run(t, r, "info", "supports")
	if want := time.Unix(1763300000, 0).Format("2006-01-02 15:04:05"); jsonx.Str(d, "scanned_at") != want {
		t.Fatalf("scanned_at %q want %q", jsonx.Str(d, "scanned_at"), want)
	}
	if jsonx.Str(d, "source") != "import:support-inventory.json" {
		t.Fatalf("source %q", jsonx.Str(d, "source"))
	}
}

func TestListingCarriesCountsBeforeItems(t *testing.T) {
	r := reg(t)
	run(t, r, "info", "import", "--kind", "supports", filepath.Join(dataDir, "support-inventory.json"))
	_, d, raw := run(t, r, "info", "supports", "--rarity", "SSR")
	c := jsonx.Obj(d, "counts")
	if n, _ := jsonx.Int(c, "total"); n != 3 {
		t.Fatalf("total %v", c)
	}
	attrs := map[string]int64{}
	for _, it := range jsonx.List(d, "items") {
		attrs[jsonx.Str(it.(*jsonx.Object), "attr")]++
	}
	byAttr := jsonx.Obj(c, "by_attr")
	for k, n := range attrs {
		if got, _ := jsonx.Int(byAttr, k); got != n {
			t.Fatalf("by_attr %v vs %v", byAttr, attrs)
		}
	}
	if got, _ := jsonx.Marshal(jsonx.Obj(c, "by_rarity")); string(got) != `{"SSR":3}` {
		t.Fatalf("by_rarity %s", got)
	}
	if strings.Index(raw, `"counts"`) > strings.Index(raw, `"items"`) {
		t.Fatal("counts must come before items")
	}
	if strings.Join(d.Keys(), ",") != "kind,scanned_at,complete,source,total,counts,items" {
		t.Fatalf("keys %v", d.Keys())
	}
}

func TestGameSettingsImportAndVerdict(t *testing.T) {
	r := reg(t)
	p := filepath.Join(t.TempDir(), "s.json")
	inv := `{"items": {"未読早送り": "ON", "支援事件剧情跳过": "OFF", "开始说明剧情跳过": "ON", "定期公演剧情跳过": "ON", "親愛度映像跳过": null}, "verdict": "设置被重置：1 项 OFF", "ok": false}`
	if err := os.WriteFile(p, []byte(inv), 0o600); err != nil {
		t.Fatal(err)
	}
	run(t, r, "info", "import", "--kind", "game_settings", p)
	_, d, _ := run(t, r, "info", "game-settings")
	if jsonx.Str(d, "verdict") != "设置被重置：1 项 OFF" {
		t.Fatalf("verdict %v", d)
	}
	items := jsonx.List(d, "items")
	if got, _ := jsonx.Marshal(items[1]); string(got) != `{"item":"支援事件剧情跳过","state":"OFF"}` {
		t.Fatalf("item %s", got)
	}
	if v, _ := d.Get("complete"); v != false { // 有一项读不到 → 没检查全
		t.Fatalf("complete %v", v)
	}
}

func TestImportBadFile(t *testing.T) {
	r := reg(t)
	code, _, raw := run(t, r, "info", "import", "--kind", "supports", filepath.Join(t.TempDir(), "nope.json"))
	if code != 1 || !strings.Contains(raw, `"bad_file"`) {
		t.Fatalf("%d %s", code, raw)
	}
}

func TestCountsKeysAndUnknown(t *testing.T) {
	items := []any{
		jsonx.NewObject().Set("rarity", "SR").Set("attr", nil),
		jsonx.NewObject().Set("rarity", "SSR"),
		jsonx.NewObject().Set("rarity", "SSR").Set("level", 60),
	}
	got, _ := jsonx.Marshal(Counts(items))
	// SSR 两张排前面；attr 只有一行有这个键（值是 null）也要统计，算「未知」
	if string(got) != `{"total":3,"by_rarity":{"SSR":2,"SR":1},"by_attr":{"未知":3}}` {
		t.Fatalf("counts %s", got)
	}
}
