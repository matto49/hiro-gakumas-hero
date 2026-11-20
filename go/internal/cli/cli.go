// Package cli 是 hiro 命令的骨架：命令登记、参数解析、JSON 信封、退出码、待确认流程，同 Python 版
// hiro.cli.registry + hiro.cli.main。各命令包（info、catalog、run…）往 Registry 里登记自己的命令。
//
// 退出码：0 成功；1 命令失败（信封 ok=false）；2 用法错误 / 未知命令；3 待确认（信封 ok=true，data.pending 有编号）。
package cli

import (
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pending"
)

// Risk 是命令的风险等级。
type Risk string

const (
	// Read 只读（查信息、看状态），直接执行。
	Read Risk = "read"
	// Act 会动游戏（开局、扫描）：第一次执行返回待确认。
	Act Risk = "act"
)

// ArgType 是参数的类型。
type ArgType string

const (
	String ArgType = "string"
	Int    ArgType = "int"
	Bool   ArgType = "bool"
)

// PendingExit 是待确认的退出码。
const PendingExit = 3

// forbiddenWords：花ジュエル、放弃培育的命令不存在，也不允许登记（同 Python FORBIDDEN_WORDS）。
var forbiddenWords = []string{"jewel", "discard", "retire", "ジュエル", "破棄", "リタイア"}

// agentReadonlyDeny：桌面程序以 Codex / Claude 命令行当对话后端时设 HIRO_AGENT_SANDBOX=1，这些命令和所有 Act 命令一律拒绝。
var agentReadonlyDeny = map[string]bool{"confirm": true, "cancel": true, "secret copy": true, "secret set": true, "secret delete": true}

// Arg 是一个参数。位置参数写 "query"，选项写 "--rarity"。
type Arg struct {
	Name     string
	Help     string
	Type     ArgType // 缺省 String
	Choices  []string
	Required bool
	Default  any
	NoAgent  bool // 给内置 agent 的工具描述里不带这个参数（会卡住对话的，例如 --follow）
}

// Positional 是不是位置参数。
func (a Arg) Positional() bool { return !strings.HasPrefix(a.Name, "-") }

// Dest 是参数在 Args 里的键：去掉 --，- 换成 _。
func (a Arg) Dest() string { return strings.ReplaceAll(strings.TrimLeft(a.Name, "-"), "-", "_") }

func (a Arg) typ() ArgType {
	if a.Type == "" {
		return String
	}
	return a.Type
}

func (a Arg) describe() *jsonx.Object {
	o := jsonx.NewObject().Set("name", a.Name).Set("help", a.Help).Set("type", string(a.typ())).
		Set("required", a.Required || a.Positional()).Set("agent", !a.NoAgent)
	if len(a.Choices) > 0 {
		o.Set("choices", a.Choices)
	}
	if a.Default != nil {
		o.Set("default", a.Default)
	}
	return o
}

// Args 是解析好的参数：键是 Arg.Dest()，没给的是 null，开关缺省 false。确认时从待确认记录里读回来（数字是 json.Number）。
type Args struct{ *jsonx.Object }

// Str 取字符串参数；没给是 ""。
func (a Args) Str(k string) string { return jsonx.Str(a.Object, k) }

// Int 取整数参数；没给 ok=false。
func (a Args) Int(k string) (int64, bool) { return jsonx.Int(a.Object, k) }

// Bool 取开关参数。
func (a Args) Bool(k string) bool { v, _ := a.Get(k); b, _ := v.(bool); return b }

// Given 这个参数给了（不是 null、不是 false）。
func (a Args) Given(k string) bool { v, _ := a.Get(k); return jsonx.Truthy(v) }

// Ctx 是命令运行时的输入输出。
type Ctx struct {
	Stdin  io.Reader
	Stdout io.Writer
}

// Handler 执行命令，返回 data（可 JSON 化）；失败返回 *clierr.Error。
type Handler func(ctx *Ctx, a Args) (any, error)

// Command 是一条命令。
type Command struct {
	Name     string // 空格分隔的子命令，例如 "info supports"
	Risk     Risk
	Help     string
	Args     []Arg
	Handler  Handler
	Summary  func(a Args) string // Act 命令：待确认卡片上写的一句话
	Precheck func(a Args) error  // Act 命令：出待确认之前先查（明显做不了就直接报错，不让用户白确认）
	NoAgent  bool                // 不给内置 agent 当工具
}

// ToolName 是给模型的工具名：空格、- 换成 _。
func (c *Command) ToolName() string {
	return strings.NewReplacer(" ", "_", "-", "_").Replace(c.Name)
}

func (c *Command) describe() *jsonx.Object {
	args := make([]any, 0, len(c.Args))
	for _, a := range c.Args {
		args = append(args, a.describe())
	}
	return jsonx.NewObject().Set("name", c.Name).Set("tool_name", c.ToolName()).Set("risk", string(c.Risk)).
		Set("help", c.Help).Set("args", args).Set("agent", !c.NoAgent)
}

// Registry 是全部命令（按登记顺序）。
type Registry struct {
	order []*Command
	byKey map[string]*Command
}

// New 建一个登记了内置命令（tools、confirm、cancel、pending list）的 Registry。
func New() *Registry {
	r := &Registry{byKey: map[string]*Command{}}
	r.builtins()
	return r
}

// Register 登记一条命令。碰到禁止事项、重名、缺处理函数都拒绝。
func (r *Registry) Register(c *Command) error {
	low := strings.ToLower(c.Name)
	for _, w := range forbiddenWords {
		if strings.Contains(low, w) {
			return fmt.Errorf("命令 %q 碰到禁止事项（花ジュエル / 放弃培育），不允许登记", c.Name)
		}
	}
	if c.Handler == nil {
		return fmt.Errorf("命令 %q 没有处理函数", c.Name)
	}
	if _, dup := r.byKey[c.Name]; dup {
		return fmt.Errorf("命令 %q 登记了两次", c.Name)
	}
	if c.Risk != Read && c.Risk != Act {
		return fmt.Errorf("命令 %q 的风险等级不对：%q", c.Name, c.Risk)
	}
	r.order = append(r.order, c)
	r.byKey[c.Name] = c
	return nil
}

// MustRegister 同 Register，出错就 panic（程序启动时登记，出错是代码写错了）。
func (r *Registry) MustRegister(cs ...*Command) {
	for _, c := range cs {
		if err := r.Register(c); err != nil {
			panic(err)
		}
	}
}

// Commands 按登记顺序返回全部命令。
func (r *Registry) Commands() []*Command { return append([]*Command(nil), r.order...) }

// Get 按名字取命令。
func (r *Registry) Get(name string) *Command { return r.byKey[name] }

// resolve 最长前缀匹配命令名：["info","supports","--rarity","SSR"] → (info supports, ["--rarity","SSR"])。
func (r *Registry) resolve(argv []string) (*Command, []string) {
	for n := len(argv); n > 0; n-- {
		if c := r.byKey[strings.Join(argv[:n], " ")]; c != nil {
			return c, argv[n:]
		}
	}
	return nil, argv
}

// ---------------------------------------------------------------- 信封

// Envelope 是一条命令的输出。
type Envelope struct {
	OK    bool
	Data  any
	Error *clierr.Error
}

// MarshalJSON 输出 {"ok": true, "data": …} 或 {"ok": false, "error": {"code","message","hint"}}。
func (e Envelope) MarshalJSON() ([]byte, error) {
	o := jsonx.NewObject().Set("ok", e.OK)
	if e.OK {
		o.Set("data", e.Data)
	} else {
		o.Set("error", jsonx.NewObject().Set("code", e.Error.Code).Set("message", e.Error.Message).Set("hint", e.Error.Hint))
	}
	return jsonx.Marshal(o)
}

func failed(err error) (int, Envelope) {
	e := clierr.As(err)
	exit := e.Exit
	if exit == 0 {
		exit = 1
	}
	return exit, Envelope{Error: e}
}

// ---------------------------------------------------------------- 运行

// Main 跑一条命令，把信封写到 ctx.Stdout（一行 JSON），返回退出码。
func (r *Registry) Main(argv []string, ctx *Ctx) int {
	code, env := r.Run(argv, ctx)
	out, err := jsonx.Marshal(env)
	if err != nil {
		code, env = failed(fmt.Errorf("输出不能写成 JSON：%w", err))
		out, _ = jsonx.Marshal(env)
	}
	_, _ = fmt.Fprintf(ctx.Stdout, "%s\n", out)
	return code
}

// Run 跑一条命令，返回退出码和信封。
func (r *Registry) Run(argv []string, ctx *Ctx) (int, Envelope) {
	cmd, rest := r.resolve(argv)
	if cmd == nil {
		what := strings.Join(argv, " ")
		if what == "" {
			what = "（空）"
		}
		return failed(clierr.Usage("unknown_command", "没有这条命令："+what, "运行 hiro tools 查看全部命令"))
	}
	args, err := parse(cmd, rest)
	if err != nil {
		return failed(clierr.Usage("bad_args", cmd.Name+" 的参数不对："+err.Error(), "运行 hiro tools 查看 "+cmd.Name+" 的参数"))
	}
	if os.Getenv("HIRO_AGENT_SANDBOX") != "" && (cmd.Risk == Act || agentReadonlyDeny[cmd.Name]) {
		return failed(clierr.New("agent_readonly", "程序内置的对话模式只能查信息，不能运行 hiro "+cmd.Name,
			"要开局或扫描时，在回答里单独写一行 HIRO_ACTION: run start <参数>，程序会给玩家出确认卡"))
	}
	if cmd.Risk == Act {
		if cmd.Precheck != nil {
			if err := cmd.Precheck(args); err != nil {
				return failed(err)
			}
		}
		summary := "执行 hiro " + cmd.Name
		if cmd.Summary != nil {
			summary = cmd.Summary(args)
		}
		rec, err := pending.Create(cmd.Name, args.Object, summary)
		if err != nil {
			return failed(err)
		}
		return PendingExit, Envelope{OK: true, Data: jsonx.NewObject().Set("pending", rec.Brief()).
			Set("message", fmt.Sprintf("需要用户确认：%s。用户同意后运行 hiro confirm %s；不要自己确认。", summary, rec.ID))}
	}
	return execute(cmd, args, ctx)
}

func execute(cmd *Command, args Args, ctx *Ctx) (int, Envelope) {
	data, err := cmd.Handler(ctx, args)
	if err != nil {
		return failed(err)
	}
	return 0, Envelope{OK: true, Data: data}
}

// parse 解析参数，行为同 Python 的 argparse（不支持选项缩写）：选项 --x v 或 --x=v；开关不带值；-- 之后都当位置参数。
func parse(cmd *Command, rest []string) (Args, error) {
	opts := map[string]Arg{}
	var positional []Arg
	vals := map[string]any{}
	for _, a := range cmd.Args {
		if a.Positional() {
			positional = append(positional, a)
		} else {
			opts[a.Name] = a
		}
	}
	var pos []string
	onlyPositional := false
	for i := 0; i < len(rest); i++ {
		tok := rest[i]
		if onlyPositional || !strings.HasPrefix(tok, "--") {
			pos = append(pos, tok)
			continue
		}
		if tok == "--" {
			onlyPositional = true
			continue
		}
		name, value, hasValue := strings.Cut(tok, "=")
		a, ok := opts[name]
		if !ok {
			return Args{}, fmt.Errorf("不认识的参数 %s", name)
		}
		if a.typ() == Bool {
			if hasValue {
				return Args{}, fmt.Errorf("%s 是开关，不带值", name)
			}
			vals[a.Dest()] = true
			continue
		}
		if !hasValue {
			if i+1 >= len(rest) {
				return Args{}, fmt.Errorf("%s 后面要跟一个值", name)
			}
			i++
			value = rest[i]
		}
		v, err := convert(a, value)
		if err != nil {
			return Args{}, err
		}
		vals[a.Dest()] = v
	}
	if len(pos) > len(positional) {
		return Args{}, fmt.Errorf("多余的参数：%s", strings.Join(pos[len(positional):], " "))
	}
	for i, a := range positional {
		if i < len(pos) {
			v, err := convert(a, pos[i])
			if err != nil {
				return Args{}, err
			}
			vals[a.Dest()] = v
		}
	}
	o := jsonx.NewObject()
	var missing []string
	for _, a := range cmd.Args {
		v, given := vals[a.Dest()]
		switch {
		case given:
			o.Set(a.Dest(), v)
		case a.typ() == Bool:
			o.Set(a.Dest(), false)
		case a.Default != nil:
			o.Set(a.Dest(), a.Default)
		default:
			if a.Required || a.Positional() {
				missing = append(missing, a.Name)
			}
			o.Set(a.Dest(), nil)
		}
	}
	if len(missing) > 0 {
		return Args{}, fmt.Errorf("少了参数：%s", strings.Join(missing, "、"))
	}
	return Args{o}, nil
}

func convert(a Arg, s string) (any, error) {
	var v any = s
	if a.typ() == Int {
		n, err := strconv.Atoi(s)
		if err != nil {
			return nil, fmt.Errorf("%s 要是整数：%s", a.Name, s)
		}
		v = n
	}
	if len(a.Choices) > 0 {
		for _, c := range a.Choices {
			if c == s {
				return v, nil
			}
		}
		return nil, fmt.Errorf("%s 只能是 %s：%s", a.Name, strings.Join(a.Choices, " / "), s)
	}
	return v, nil
}

// ---------------------------------------------------------------- 内置命令

func (r *Registry) builtins() {
	r.MustRegister(
		&Command{
			Name: "tools", Risk: Read,
			Help: "列出全部命令：名字、风险等级（read 直接执行 / act 先返回待确认）、说明、参数。",
			Handler: func(*Ctx, Args) (any, error) {
				tools := make([]any, 0, len(r.order))
				for _, c := range r.order {
					tools = append(tools, c.describe())
				}
				return jsonx.NewObject().Set("tools", tools), nil
			},
		},
		&Command{
			Name: "confirm", Risk: Read, NoAgent: true,
			Help: "用户同意后执行一条待确认的命令（只能由用户发起，agent 不要调用）。",
			Args: []Arg{{Name: "id", Help: "待确认编号"}},
			Handler: func(ctx *Ctx, a Args) (any, error) {
				rec, err := pending.Take(a.Str("id"))
				if err != nil {
					return nil, err
				}
				cmd := r.byKey[rec.Command]
				if cmd == nil {
					return nil, clierr.New("unknown_command", "待确认里的命令已经不存在："+rec.Command, "重新发起")
				}
				code, env := execute(cmd, Args{rec.Args}, ctx)
				if !env.OK {
					e := *env.Error
					e.Exit = code
					return nil, &e
				}
				return env.Data, nil
			},
		},
		&Command{
			Name: "cancel", Risk: Read,
			Help: "取消一条待确认（用户说「先不」时）。",
			Args: []Arg{{Name: "id", Help: "待确认编号"}},
			Handler: func(_ *Ctx, a Args) (any, error) {
				return jsonx.NewObject().Set("cancelled", pending.Cancel(a.Str("id"))), nil
			},
		},
		&Command{
			Name: "pending list", Risk: Read,
			Help: "列出还没确认、没过期的待确认。",
			Handler: func(*Ctx, Args) (any, error) {
				list, err := pending.Listing()
				if err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("pending", list), nil
			},
		},
	)
}
