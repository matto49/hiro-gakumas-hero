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
 "memories": {}}`

func server(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/catalog" && r.URL.Query().Get("lang") == "zh":
			_, _ = w.Write([]byte(strings.Replace(catJA, `"name": "エウレカ！", "rarity"`, `"name": "尤里卡！", "rarity"`, 1)))
		case r.URL.Path == "/api/catalog":
			if r.Header.Get("User-Agent") != "hiro-console" {
				http.Error(w, "ua", http.StatusForbidden)
				return
			}
			_, _ = w.Write([]byte(catJA))
		case r.URL.Path == "/sprites/img_general_pitem_3-076.png":
			_, _ = w.Write([]byte("\x89PNG fake"))
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
