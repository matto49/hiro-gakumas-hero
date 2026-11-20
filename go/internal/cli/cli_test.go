package cli

import (
	"bytes"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// run 跑一条命令，返回退出码和解析后的信封。
func run(t *testing.T, r *Registry, argv ...string) (int, *jsonx.Object) {
	t.Helper()
	var out bytes.Buffer
	code := r.Main(argv, &Ctx{Stdout: &out, Stdin: strings.NewReader("")})
	env, err := jsonx.DecodeObject(bytes.TrimSpace(out.Bytes()))
	if err != nil {
		t.Fatalf("输出不是一个 JSON 对象：%q（%v）", out.String(), err)
	}
	return code, env
}

func data(env *jsonx.Object) *jsonx.Object { return jsonx.Obj(env, "data") }

func errCode(env *jsonx.Object) string { return jsonx.Str(jsonx.Obj(env, "error"), "code") }

func newReg(t *testing.T) (*Registry, *[]int64) {
	t.Helper()
	t.Setenv("HIRO_HOME", t.TempDir())
	t.Setenv("HIRO_AGENT_SANDBOX", "")
	r := New()
	calls := &[]int64{}
	r.MustRegister(&Command{
		Name: "test poke", Risk: Act, Help: "测试用：会动游戏的命令",
		Args:    []Arg{{Name: "--times", Help: "次数", Type: Int, Default: 1}},
		Summary: func(a Args) string { n, _ := a.Int("times"); return "戳 " + itoa(n) + " 下" },
		Handler: func(_ *Ctx, a Args) (any, error) {
			n, _ := a.Int("times")
			*calls = append(*calls, n)
			return jsonx.NewObject().Set("poked", n), nil
		},
	})
	r.MustRegister(&Command{
		Name: "info supports", Risk: Read, Help: "支援卡",
		Args: []Arg{
			{Name: "--rarity", Help: "稀有度", Choices: []string{"SSR", "SR", "R"}},
			{Name: "--name", Help: "名字"},
			{Name: "--all", Help: "全部", Type: Bool},
		},
		Handler: func(_ *Ctx, a Args) (any, error) { return a.Object, nil },
	})
	r.MustRegister(&Command{
		Name: "catalog search", Risk: Read, Help: "搜",
		Args:    []Arg{{Name: "query", Help: "词"}, {Name: "--lang", Help: "语言", Choices: []string{"ja", "zh"}, Default: "ja"}},
		Handler: func(_ *Ctx, a Args) (any, error) { return a.Object, nil },
	})
	r.MustRegister(&Command{
		Name: "fails", Risk: Read, Help: "总是失败",
		Handler: func(*Ctx, Args) (any, error) { return nil, clierr.Usage("bad_args", "不行", "换个参数") },
	})
	return r, calls
}

func itoa(n int64) string { b, _ := jsonx.Marshal(n); return string(b) }

func TestToolsListsItselfAsRead(t *testing.T) {
	r, _ := newReg(t)
	code, env := run(t, r, "tools")
	if code != 0 || env.Has("error") {
		t.Fatalf("code %d env %v", code, env)
	}
	tools := map[string]*jsonx.Object{}
	for _, v := range jsonx.List(data(env), "tools") {
		o := v.(*jsonx.Object)
		tools[jsonx.Str(o, "name")] = o
	}
	if jsonx.Str(tools["tools"], "risk") != "read" || !tools["tools"].Has("help") || !tools["tools"].Has("args") {
		t.Fatalf("tools entry %v", tools["tools"])
	}
	// 描述的字段和顺序同 Python Command.describe / Arg.describe
	if got := tools["catalog search"].Keys(); strings.Join(got, ",") != "name,tool_name,risk,help,args,agent" {
		t.Fatalf("keys %v", got)
	}
	if jsonx.Str(tools["catalog search"], "tool_name") != "catalog_search" {
		t.Fatal("tool_name")
	}
	q := jsonx.List(tools["catalog search"], "args")[0].(*jsonx.Object)
	lang := jsonx.List(tools["catalog search"], "args")[1].(*jsonx.Object)
	if strings.Join(q.Keys(), ",") != "name,help,type,required,agent" || q.Has("default") {
		t.Fatalf("query describe %v", q.Keys())
	}
	if v, _ := q.Get("required"); v != true {
		t.Fatal("positional is required")
	}
	if strings.Join(lang.Keys(), ",") != "name,help,type,required,agent,choices,default" || jsonx.Str(lang, "default") != "ja" {
		t.Fatalf("lang describe %v", lang.Keys())
	}
	if v, _ := tools["confirm"].Get("agent"); v != false {
		t.Fatal("confirm is not an agent tool")
	}
}

func TestUnknownCommand(t *testing.T) {
	r, _ := newReg(t)
	code, env := run(t, r, "no-such-thing")
	if code != 2 || errCode(env) != "unknown_command" || jsonx.Str(jsonx.Obj(env, "error"), "hint") == "" {
		t.Fatalf("code %d env %v", code, env)
	}
	if v, _ := env.Get("ok"); v != false {
		t.Fatal("ok should be false")
	}
	if code, env := run(t, r); code != 2 || !strings.Contains(jsonx.Str(jsonx.Obj(env, "error"), "message"), "（空）") {
		t.Fatalf("empty argv: %d %v", code, env)
	}
}

func TestForbiddenCommandsAreRefused(t *testing.T) {
	r := New()
	for _, name := range []string{"produce discard", "buy jewel", "retire", "ジュエル 使う", "破棄", "リタイア"} {
		if err := r.Register(&Command{Name: name, Risk: Act, Handler: func(*Ctx, Args) (any, error) { return nil, nil }}); err == nil {
			t.Fatalf("%q should be refused", name)
		}
	}
}

func TestParsing(t *testing.T) {
	r, _ := newReg(t)
	code, env := run(t, r, "info", "supports", "--rarity", "SR", "--name=美鈴", "--all")
	if code != 0 {
		t.Fatalf("code %d %v", code, env)
	}
	got, _ := jsonx.Marshal(data(env))
	if string(got) != `{"rarity":"SR","name":"美鈴","all":true}` {
		t.Fatalf("args %s", got)
	}
	// 没给的是 null，bool 缺省 false（同 argparse 的 vars(namespace)）
	_, env = run(t, r, "info", "supports")
	got, _ = jsonx.Marshal(data(env))
	if string(got) != `{"rarity":null,"name":null,"all":false}` {
		t.Fatalf("defaults %s", got)
	}
	_, env = run(t, r, "catalog", "search", "エウレカ")
	got, _ = jsonx.Marshal(data(env))
	if string(got) != `{"query":"エウレカ","lang":"ja"}` {
		t.Fatalf("positional %s", got)
	}
	for _, bad := range [][]string{
		{"info", "supports", "--rarity", "UR"}, // 不在可选值里
		{"info", "supports", "--nope"},         // 不认识的参数
		{"info", "supports", "--rarity"},       // 少了值
		{"info", "supports", "--all=1"},        // 开关不带值
		{"catalog", "search"},                  // 少了位置参数
		{"catalog", "search", "a", "b"},        // 多余的参数
		{"test", "poke", "--times", "three"},   // 不是整数
	} {
		code, env := run(t, r, bad...)
		if code != 2 || errCode(env) != "bad_args" {
			t.Fatalf("%v: code %d env %v", bad, code, env)
		}
	}
}

func TestHandlerErrorKeepsExitCode(t *testing.T) {
	r, _ := newReg(t)
	code, env := run(t, r, "fails")
	if code != 2 || errCode(env) != "bad_args" || jsonx.Str(jsonx.Obj(env, "error"), "hint") != "换个参数" {
		t.Fatalf("code %d env %v", code, env)
	}
}

func TestActReturnsPendingWithoutRunning(t *testing.T) {
	r, calls := newReg(t)
	code, env := run(t, r, "test", "poke", "--times", "3")
	if code != 3 || len(*calls) != 0 {
		t.Fatalf("code %d calls %v", code, *calls)
	}
	p := jsonx.Obj(data(env), "pending")
	if jsonx.Str(p, "summary") != "戳 3 下" || jsonx.Str(p, "command") != "test poke" || jsonx.Str(p, "id") == "" || !p.Has("expires_at") {
		t.Fatalf("pending %v", p)
	}
	if !strings.Contains(jsonx.Str(data(env), "message"), "hiro confirm "+jsonx.Str(p, "id")) {
		t.Fatalf("message %q", jsonx.Str(data(env), "message"))
	}
}

func TestConfirmRunsOnce(t *testing.T) {
	r, calls := newReg(t)
	_, env := run(t, r, "test", "poke", "--times", "2")
	id := jsonx.Str(jsonx.Obj(data(env), "pending"), "id")
	code, env := run(t, r, "confirm", id)
	if code != 0 {
		t.Fatalf("confirm %d %v", code, env)
	}
	if got, _ := jsonx.Marshal(data(env)); string(got) != `{"poked":2}` || len(*calls) != 1 || (*calls)[0] != 2 {
		t.Fatalf("data %s calls %v", got, *calls)
	}
	if _, env := run(t, r, "confirm", id); errCode(env) != "pending_unknown" || len(*calls) != 1 {
		t.Fatalf("second confirm %v", env)
	}
}

func TestCancelAndList(t *testing.T) {
	r, _ := newReg(t)
	_, env := run(t, r, "test", "poke")
	id := jsonx.Str(jsonx.Obj(data(env), "pending"), "id")
	_, env = run(t, r, "pending", "list")
	list := jsonx.List(data(env), "pending")
	if len(list) != 1 || jsonx.Str(list[0].(*jsonx.Object), "id") != id {
		t.Fatalf("list %v", list)
	}
	_, env = run(t, r, "cancel", id)
	if v, _ := data(env).Get("cancelled"); v != true {
		t.Fatalf("cancel %v", env)
	}
	_, env = run(t, r, "pending", "list")
	if len(jsonx.List(data(env), "pending")) != 0 {
		t.Fatal("still listed")
	}
}

func TestBadIDIsRejected(t *testing.T) {
	r, _ := newReg(t)
	if _, env := run(t, r, "confirm", "../../etc/passwd"); errCode(env) != "pending_unknown" {
		t.Fatalf("%v", env)
	}
}

func TestAgentSandboxRefuses(t *testing.T) {
	r, calls := newReg(t)
	r.MustRegister(&Command{
		Name: "secret copy", Risk: Read, Args: []Arg{{Name: "name", Help: "名字"}},
		Handler: func(*Ctx, Args) (any, error) { return nil, nil },
	})
	_, env := run(t, r, "test", "poke")
	id := jsonx.Str(jsonx.Obj(data(env), "pending"), "id")
	t.Setenv("HIRO_AGENT_SANDBOX", "1")
	for _, argv := range [][]string{{"confirm", id}, {"test", "poke"}, {"secret", "copy", "game-password"}, {"cancel", id}} {
		_, env := run(t, r, argv...)
		if errCode(env) != "agent_readonly" || !strings.Contains(jsonx.Str(jsonx.Obj(env, "error"), "hint"), "HIRO_ACTION") {
			t.Fatalf("%v: %v", argv, env)
		}
	}
	if code, _ := run(t, r, "pending", "list"); code != 0 {
		t.Fatal("pending list should still work")
	}
	if len(*calls) != 0 {
		t.Fatal("nothing should run")
	}
}

func TestPrecheckStopsBeforePending(t *testing.T) {
	r, _ := newReg(t)
	r.MustRegister(&Command{
		Name: "run start", Risk: Act, Args: []Arg{{Name: "kind", Help: "种类"}},
		Precheck: func(a Args) error { return clierr.Usage("bad_args", "不能开 "+a.Str("kind"), "换一个") },
		Handler:  func(*Ctx, Args) (any, error) { return nil, nil },
	})
	code, env := run(t, r, "run", "start", "x")
	if code != 2 || errCode(env) != "bad_args" {
		t.Fatalf("%d %v", code, env)
	}
	_, env = run(t, r, "pending", "list")
	if len(jsonx.List(data(env), "pending")) != 0 {
		t.Fatal("no pending should be created")
	}
}

func TestArgvRoundTrip(t *testing.T) {
	r, _ := newReg(t)
	c := r.Get("info supports")
	a, err := parse(c, []string{"--name", "美 鈴", "--all", "--rarity=SR"})
	if err != nil {
		t.Fatal(err)
	}
	got := strings.Join(c.Argv(a), "|")
	if got != "info|supports|--rarity|SR|--name|美 鈴|--all" {
		t.Fatalf("argv %q", got)
	}
	s := r.Get("catalog search")
	a, _ = parse(s, []string{"x"})
	if got := strings.Join(s.Argv(a), "|"); got != "catalog|search|x|--lang|ja" {
		t.Fatalf("argv %q", got)
	}
	p := r.Get("test poke")
	a, _ = parse(p, []string{"--times", "4"})
	if got := strings.Join(p.Argv(a), "|"); got != "test|poke|--times|4" {
		t.Fatalf("argv %q", got)
	}
}
