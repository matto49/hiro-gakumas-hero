package secret

import (
	"bytes"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

const value = "p@ss-WORD-1234"

type clipCall struct {
	value      string
	clearAfter int
}

func setup(t *testing.T) (*cli.Registry, *[]clipCall) {
	t.Helper()
	t.Setenv("HIRO_HOME", t.TempDir())
	t.Setenv("HIRO_SECRET_BACKEND", "memory")
	resetMemory()
	calls := &[]clipCall{}
	old := writeClipboard
	writeClipboard = func(v string, clearAfter int) error { *calls = append(*calls, clipCall{v, clearAfter}); return nil }
	t.Cleanup(func() { writeClipboard = old })
	r := cli.New()
	Register(r)
	return r, calls
}

func run(t *testing.T, r *cli.Registry, stdin string, argv ...string) (int, *jsonx.Object, string) {
	t.Helper()
	var out bytes.Buffer
	code := r.Main(argv, &cli.Ctx{Stdout: &out, Stdin: strings.NewReader(stdin)})
	env, err := jsonx.DecodeObject(bytes.TrimSpace(out.Bytes()))
	if err != nil {
		t.Fatalf("not json %q", out.String())
	}
	return code, env, out.String()
}

func TestSetReadsStdinAndListShowsNamesOnly(t *testing.T) {
	r, _ := setup(t)
	code, _, raw := run(t, r, value+"\n", "secret", "set", "game-password")
	if code != 0 || strings.Contains(raw, value) {
		t.Fatalf("%d %s", code, raw)
	}
	_, env, raw := run(t, r, "", "secret", "list")
	if got, _ := jsonx.Marshal(jsonx.List(jsonx.Obj(env, "data"), "secrets")); string(got) != `[{"name":"game-password","saved":true}]` {
		t.Fatalf("list %s", got)
	}
	if strings.Contains(raw, value) {
		t.Fatal("value leaked")
	}
	if v, _ := backend().Get("game-password"); v != value { // 结尾换行去掉
		t.Fatalf("stored %q", v)
	}
}

func TestCopyPutsValueOnClipboardWithoutPrintingIt(t *testing.T) {
	r, clip := setup(t)
	run(t, r, value, "secret", "set", "game-password")
	code, env, raw := run(t, r, "", "secret", "copy", "game-password")
	if code != 0 || strings.Contains(raw, value) {
		t.Fatalf("%d %s", code, raw)
	}
	if got, _ := jsonx.Marshal(jsonx.Obj(env, "data")); string(got) != `{"copied":"game-password","clears_in":45}` {
		t.Fatalf("data %s", got)
	}
	if len(*clip) != 1 || (*clip)[0] != (clipCall{value, 45}) {
		t.Fatalf("clipboard %v", *clip)
	}
}

func TestCopyMissingSecretIsExplained(t *testing.T) {
	r, clip := setup(t)
	_, env, _ := run(t, r, "", "secret", "copy", "game-password")
	e := jsonx.Obj(env, "error")
	if jsonx.Str(e, "code") != "secret_missing" || !strings.Contains(jsonx.Str(e, "hint"), "设置") || len(*clip) != 0 {
		t.Fatalf("%v %v", env, *clip)
	}
}

func TestOnlyTheseSecretCommandsExist(t *testing.T) {
	r, _ := setup(t)
	var names []string
	for _, c := range r.Commands() {
		if strings.HasPrefix(c.Name, "secret") {
			names = append(names, c.Name)
			if (c.Name == "secret set" || c.Name == "secret delete") != c.NoAgent {
				t.Fatalf("%s agent flag", c.Name)
			}
		}
	}
	if strings.Join(names, ",") != "secret set,secret list,secret copy,secret delete" {
		t.Fatalf("%v", names)
	}
}

func TestEmptyValueAndBadNameAreRefused(t *testing.T) {
	r, _ := setup(t)
	if _, env, _ := run(t, r, "\n", "secret", "set", "game-password"); jsonx.Str(jsonx.Obj(env, "error"), "code") != "empty_secret" {
		t.Fatalf("%v", env)
	}
	for _, bad := range []string{"../x", "", strings.Repeat("a", 65), "a b"} {
		if _, env, _ := run(t, r, value, "secret", "set", bad); jsonx.Str(jsonx.Obj(env, "error"), "code") != "bad_args" {
			t.Fatalf("%q: %v", bad, env)
		}
	}
}

func TestDeleteRemovesIt(t *testing.T) {
	r, _ := setup(t)
	run(t, r, value, "secret", "set", "game-account")
	run(t, r, "", "secret", "delete", "game-account")
	_, env, _ := run(t, r, "", "secret", "list")
	if len(jsonx.List(jsonx.Obj(env, "data"), "secrets")) != 0 {
		t.Fatalf("%v", env)
	}
	if v, _ := backend().Get("game-account"); v != "" {
		t.Fatal("still stored")
	}
}

func TestUTF16RoundTrip(t *testing.T) {
	for _, s := range []string{"", "abc", "パスワード", "😀x"} {
		if got := fromUTF16(toUTF16(s)); got != s {
			t.Fatalf("%q → %q", s, got)
		}
	}
	if b := toUTF16("ab"); len(b) != 4 || b[0] != 'a' || b[1] != 0 {
		t.Fatalf("little endian %v", b)
	}
}
