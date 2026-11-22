package catalog

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

const catJA = `{"schema": 18,
 "cards": {"p_card-01-ido-3_105": {"name": "エウレカ！", "rarity": "SSR", "plan": "PLAN2", "category": "ACTIVE",
   "asset": "img_general_skillcard_ido-3_105", "up": {"0": {"name": "エウレカ！", "text": "好調3ターン"}}}},
 "items": {"pitem_3-076": {"name": "選びとった熱", "rarity": "SSR", "plan": "PLAN2", "text": "試験開始時", "asset": "img_general_pitem_3-076"}},
 "drinks": {}, "enchants": {}, "idols": {"i_card-shro-3-007": {"name": "【Campus mode!!】篠澤広", "rarity": "SSR", "plan": "PLAN2"}},
 "memories": {"ability-1": {"name": "", "rarity": "R", "text": "初期ビジュアル上昇+15"}}}`

func server(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/catalog" && r.URL.Query().Get("lang") == "zh":
			_, _ = w.Write([]byte(strings.Replace(strings.Replace(catJA, `"name": "エウレカ！", "rarity"`, `"name": "尤里卡！", "rarity"`, 1), `"text": "初期ビジュアル上昇+15"`, `"text": "初始视觉+15"`, 1)))
		case r.URL.Path == "/api/catalog":
			if r.Header.Get("User-Agent") != "hiro-console" {
				http.Error(w, "ua", http.StatusForbidden)
				return
			}
			_, _ = w.Write([]byte(catJA))
		case r.URL.Path == "/sprites/img_general_pitem_3-076.png":
			_, _ = w.Write([]byte("\x89PNG fake"))
		case r.URL.Path == "/sprites/support-s_card-3-0000.webp":
			_, _ = w.Write([]byte("RIFF fake webp"))
		case r.URL.Path == "/api/supports":
			_, _ = w.Write([]byte(supportsJSON))
		default:
			http.NotFound(w, r)
		}
	}))
	t.Setenv("HIRO_CATALOG_URL", srv.URL)
	return srv
}

func reg(t *testing.T) *cli.Registry {
	t.Helper()
	t.Setenv("HIRO_HOME", t.TempDir())
	r := cli.New()
	Register(r)
	return r
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

func TestSearchBeforeSyncExplains(t *testing.T) {
	r := reg(t)
	_, env := run(t, r, "catalog", "search", "エウレカ")
	e := jsonx.Obj(env, "error")
	if jsonx.Str(e, "code") != "catalog_missing" || !strings.Contains(jsonx.Str(e, "hint"), "hiro catalog sync") {
		t.Fatalf("%v", env)
	}
}

func TestSyncThenSearchAndGet(t *testing.T) {
	srv := server(t)
	defer srv.Close()
	r := reg(t)
	code, env := run(t, r, "catalog", "sync")
	d := jsonx.Obj(env, "data")
	if n, _ := jsonx.Int(jsonx.Obj(d, "counts"), "cards"); code != 0 || n != 1 {
		t.Fatalf("sync %d %v", code, env)
	}
	if got, _ := jsonx.Marshal(jsonx.List(d, "langs")); string(got) != `["ja","zh"]` {
		t.Fatalf("langs %s", got)
	}
	_, env = run(t, r, "catalog", "search", "エウレカ")
	hit := jsonx.List(jsonx.Obj(env, "data"), "items")[0].(*jsonx.Object)
	if jsonx.Str(hit, "id") != "p_card-01-ido-3_105" || jsonx.Str(hit, "kind") != "cards" || jsonx.Str(hit, "text") != "好調3ターン" {
		t.Fatalf("hit %v", hit)
	}
	if strings.Join(hit.Keys(), ",") != "kind,id,name,rarity,plan,text,asset" {
		t.Fatalf("keys %v", hit.Keys())
	}
	_, env = run(t, r, "catalog", "search", "尤里卡", "--lang", "zh")
	if jsonx.Str(jsonx.List(jsonx.Obj(env, "data"), "items")[0].(*jsonx.Object), "name") != "尤里卡！" {
		t.Fatalf("zh %v", env)
	}
	_, env = run(t, r, "catalog", "search", "熱", "--kind", "cards")
	if len(jsonx.List(jsonx.Obj(env, "data"), "items")) != 0 {
		t.Fatalf("kind filter %v", env)
	}
	_, env = run(t, r, "catalog", "get", "pitem_3-076")
	d = jsonx.Obj(env, "data")
	if jsonx.Str(d, "kind") != "items" || jsonx.Str(jsonx.Obj(d, "entry"), "name") != "選びとった熱" {
		t.Fatalf("get %v", d)
	}
	if _, env = run(t, r, "catalog", "get", "nope"); jsonx.Str(jsonx.Obj(env, "error"), "code") != "not_found" {
		t.Fatalf("get missing %v", env)
	}
	if IdolName("i_card-skin-shro-3-007") != "【Campus mode!!】篠澤広" || IdolName("i_card-xxx") != "i_card-xxx" {
		t.Fatal("IdolName")
	}
}

func TestSpriteDownloadsOnceAndCaches(t *testing.T) {
	srv := server(t)
	r := reg(t)
	_, env := run(t, r, "catalog", "sprite", "img_general_pitem_3-076")
	p := jsonx.Str(jsonx.Obj(env, "data"), "path")
	if b, err := os.ReadFile(p); err != nil || string(b) != "\x89PNG fake" {
		t.Fatalf("sprite %q %v", b, err)
	}
	srv.Close()
	if _, env = run(t, r, "catalog", "sprite", "img_general_pitem_3-076"); jsonx.Str(jsonx.Obj(env, "data"), "path") != p {
		t.Fatalf("cached %v", env)
	}
	if code, env := run(t, r, "catalog", "sprite", "../etc/passwd"); code != 1 || jsonx.Str(jsonx.Obj(env, "error"), "code") != "bad_args" {
		t.Fatalf("bad asset %d %v", code, env)
	}
}

func TestIdolNameWithoutCatalog(t *testing.T) {
	t.Setenv("HIRO_HOME", t.TempDir())
	if IdolName("i_card-skin-shro-3-007") != "i_card-skin-shro-3-007" {
		t.Fatal("falls back to the id")
	}
}

const supportsJSON = `{"format": "gkbench.supports.v1", "characters": {}, "source": {"master_date": "2026-11-16"},
 "cards": {"s_card-3-0000": {"name": {"ja": "あなたにも作ってあげる！", "zh": "我也会做给你的！"}, "rarity": "SSR", "type": "visual",
   "lesson": {"param": "visual", "permil": 37, "zh": "/ 概率高"}, "caps": [40, 45, 50, 55, 60],
   "abilities": [{"unlock": 1, "steps": [{"lv": 40, "zh": "初始视觉能力值上升+52"}]}], "events": [{"level": 1, "zh": "获得", "item": "pitem_3-076"}]}}}`

func TestSyncAlsoFetchesSupportsAndLookupGivesChineseNames(t *testing.T) {
	r := reg(t)
	server(t)
	if code, env := run(t, r, "catalog", "sync"); code != 0 || func() int64 { n, _ := jsonx.Int(jsonx.Obj(jsonx.Obj(env, "data"), "counts"), "supports"); return n }() != 1 {
		t.Fatalf("sync %v", env)
	}
	_, env := run(t, r, "catalog", "supports")
	card := jsonx.Obj(jsonx.Obj(jsonx.Obj(env, "data"), "cards"), "s_card-3-0000")
	if jsonx.Str(jsonx.Obj(card, "name"), "zh") != "我也会做给你的！" {
		t.Fatalf("supports %v", env)
	}
	_, env = run(t, r, "catalog", "lookup", "--ids", "p_card-01-ido-3_105,i_card-skin-shro-3-007,nope", "--lang", "zh")
	items := jsonx.Obj(jsonx.Obj(env, "data"), "items")
	if jsonx.Str(jsonx.Obj(items, "p_card-01-ido-3_105"), "name") != "尤里卡！" || jsonx.Str(jsonx.Obj(items, "p_card-01-ido-3_105"), "text") != "好調3ターン" {
		t.Fatalf("lookup %v", env)
	}
	if jsonx.Str(jsonx.Obj(items, "i_card-skin-shro-3-007"), "asset") != "idol-i_card-shro-3-007" || items.Has("nope") {
		t.Fatalf("lookup idol %v", env)
	}
	_, env = run(t, r, "catalog", "lookup", "--texts", "初期ビジュアル上昇+15\n初期ビジュアル上昇+15B9", "--lang", "zh")
	tr := jsonx.Obj(jsonx.Obj(env, "data"), "texts")
	if jsonx.Str(tr, "初期ビジュアル上昇+15") != "初始视觉+15" || tr.Len() != 1 {
		t.Fatalf("texts %v", env)
	}
}

func TestSpriteFallsBackToWebp(t *testing.T) {
	reg(t)
	server(t)
	p, err := SpritePath("support-s_card-3-0000")
	if err != nil || !strings.HasSuffix(p, ".webp") {
		t.Fatalf("%v %v", p, err)
	}
	if p2, _ := SpritePath("support-s_card-3-0000"); p2 != p {
		t.Fatal("second call must use the cached file")
	}
	if _, err := SpritePath("nothing-here"); err == nil {
		t.Fatal("missing sprite must fail")
	}
}
