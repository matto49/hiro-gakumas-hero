// Package run 是 `hiro run …`：开局 / 扫描（交给 hiro 服务跑）、看状态和事件流、暂停 / 接手 / 继续 / 停止、局后摘要和复盘页。
// 同 Python 版 hiro.cli.cmd_run。
package run

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
	"github.com/matto49/hiro-gakumas-hero/go/internal/report"
	"github.com/matto49/hiro-gakumas-hero/go/internal/service"
)

const followPoll = 500 * time.Millisecond

// paramKeys 是交给任务的参数（顺序同 Python PARAMS）。
var paramKeys = []string{
	"command", "mode", "idol", "layers", "seconds", "no_llm", "scenario", "what", "dry_run", "main_attr",
	"support_set", "memory_set", "difficulty", "items",
}

// params 只留给了的参数（不是 null、不是 false）。
func params(a cli.Args) *jsonx.Object {
	out := jsonx.NewObject()
	for _, k := range paramKeys {
		v, ok := a.Get(k)
		if !ok || v == nil || v == false {
			continue
		}
		out.Set(k, v)
	}
	return out
}

func precheck(a cli.Args) error {
	kind := a.Str("kind")
	switch {
	case kind == "fit-window" && runtime.GOOS != "windows":
		return clierr.New("windows_only", "调窗口大小只用于 Windows DMM 版", "Mac 上 PlayCover 的窗口不用调")
	case kind == "write-formation":
		if _, err := os.Stat(filepath.Join(home.Dir(), "formation-plan.json")); err != nil {
			return clierr.New("no_plan", "还没有编成方案，没东西可写", "先出一份：hiro plan formation --idol <偶像卡编号>")
		}
	case (kind == "hajime" || kind == "nia") && (a.Str("command") == "" || a.Str("command") == "new") && a.Str("idol") == "":
		return clierr.Usage("bad_args", "开新的一局要指定偶像卡", "加 --idol <偶像卡编号>，例如 i_card-skin-shro-3-007")
	case kind == "daily" && a.Str("items") != "":
		var bad []string
		for _, i := range strings.Split(a.Str("items"), ",") {
			if i != "" && !contains(service.DailyItems, i) {
				bad = append(bad, i)
			}
		}
		if len(bad) > 0 {
			return clierr.Usage("bad_args", "日常里没有这些项："+strings.Join(bad, "、"), "可选："+strings.Join(service.DailyItems, "、"))
		}
	case kind == "nia" && a.Str("difficulty") == "regular":
		return clierr.Usage("bad_args", "N.I.A 没有 REGULAR，只有 pro / master", "改成 --difficulty pro")
	}
	return nil
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func summary(a cli.Args) string {
	if k, ok := service.Kinds[a.Str("kind")]; ok {
		return k.Summary(params(a))
	}
	return "开始任务 " + a.Str("kind")
}

func jobArg() cli.Arg { return cli.Arg{Name: "--job", Help: "任务编号（缺省：当前任务）"} }

func control(action, help string) *cli.Command {
	return &cli.Command{
		Name: "run " + action, Risk: cli.Read, Help: help, Args: []cli.Arg{jobArg()},
		Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
			return service.Data(action, jsonx.NewObject().Set("job", nullable(a.Str("job"))))
		},
	}
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// Register 登记 `hiro run …` 命令。
func Register(r *cli.Registry) {
	r.MustRegister(
		&cli.Command{
			Name: "run start", Risk: cli.Act,
			Help: "开一个任务（同一时间只能一个）：hif H.I.F 培育、hajime「初」培育、nia N.I.A 培育、tower 偶像之路、daily 日常、scan-supports / scan-memories 扫描、selftest 自检。" +
				"会动游戏，第一次返回待确认，用户同意后才开始。",
			Args: []cli.Arg{
				{Name: "kind", Help: "任务种类", Choices: service.KindNames},
				{Name: "--command", Help: "hif：new 新开 / resume 接着打进行中的培育", Choices: []string{"new", "resume"}},
				{Name: "--mode", Help: "hif：qualify 选拔 / main 本战", Choices: []string{"qualify", "main"}},
				{Name: "--idol", Help: "hif / hajime / nia：偶像卡编号（如 i_card-skin-shro-3-007）；tower：路线的偶像名（如 篠澤広）"},
				{Name: "--main-attr", Help: "hif / hajime / nia：主属性（不给按编成方案）", Choices: []string{"Vo", "Da", "Vi"}},
				{Name: "--support-set", Help: "hif / hajime / nia：用游戏里第几号支援卡编成（不给按编成方案）", Type: cli.Int},
				{Name: "--memory-set", Help: "hif / hajime / nia：用第几号回忆编成", Type: cli.Int},
				{Name: "--difficulty", Help: "hajime：regular / pro / master；nia：pro / master", Choices: []string{"regular", "pro", "master"}},
				{Name: "--items", Help: "daily：只领这几项（逗号分隔：activity_funds、presents、assignment、contest、mission_reward、club_reward）"},
				{Name: "--layers", Help: "tower：打几层", Type: cli.Int},
				{Name: "--seconds", Help: "selftest：跑几秒", Type: cli.Int},
				{Name: "--no-llm", Help: "决策只用规则和算法，不调用模型", Type: cli.Bool},
				{Name: "--scenario", Help: "write-formation：写哪个模式的编成", Choices: []string{"hif", "hajime", "nia"}},
				{Name: "--what", Help: "write-formation：写哪些（support / memory，逗号分隔，缺省都写）"},
				{Name: "--dry-run", Help: "write-formation：只看差在哪几格，不改", Type: cli.Bool},
			},
			Summary: summary, Precheck: precheck,
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				// 对局用哪个模型：跟着发起开局的这个进程的环境（桌面程序按模型设置传；终端里就是自己 shell 的 HIRO_LLM）
				llm := jsonx.NewObject()
				for _, k := range service.LLMVars {
					if v := os.Getenv(k); v != "" {
						llm.Set(k, v)
					}
				}
				return service.Data("start", jsonx.NewObject().Set("kind", a.Str("kind")).Set("params", params(a)).Set("llm_env", llm))
			},
		},
		&cli.Command{
			Name: "run status", Risk: cli.Read,
			Help:    "现在在跑什么任务（状态：running / paused / takeover / stopping），以及最近 10 个任务。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) { return service.Data("status", nil) },
		},
		&cli.Command{
			Name: "run events", Risk: cli.Read,
			Help: "读一个任务的事件（每一步的日志和结果）。--since 从第几条开始；--follow 一直跟到任务结束（每条事件一行 JSON）。",
			Args: []cli.Arg{
				jobArg(),
				{Name: "--since", Help: "从第几条事件开始", Type: cli.Int, Default: 0},
				{Name: "--follow", Help: "一直跟到任务结束", Type: cli.Bool, NoAgent: true},
			},
			Handler: events,
		},
		control("pause", "暂停当前任务：主循环停在下一个检查点，不再点击。"),
		control("takeover", "用户接手：自动操作停下，用户在游戏里手动点；之后 hiro run resume 从当前画面接着打。"),
		control("resume", "继续：从当前画面重新判断在哪一页，接着自动打。"),
		control("stop", "停止当前任务（不会放弃培育：游戏停在原地，之后可以 resume 续跑）。"),
		&cli.Command{
			Name: "run report", Risk: cli.Read,
			Help: "一局的成绩摘要：步数、决策来源（规则 / 算法 / LLM / 兜底）、LLM 次数、故障恢复次数、每场考试分数。",
			Args: []cli.Arg{{Name: "--job", Help: "任务编号", Required: true}},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				return report.Summarize(a.Str("job"))
			},
		},
		&cli.Command{
			Name: "run review", Risk: cli.Read,
			Help: "生成一局的复盘页（逐回合出牌、候选、状态曲线，带截图），返回本机文件路径。只给有考试的对局。",
			Args: []cli.Arg{{Name: "--job", Help: "任务编号", Required: true}},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				jid := a.Str("job")
				if err := report.CheckJob(jid); err != nil {
					return nil, err
				}
				if len(report.Segments(report.TracesDir(), jid)) == 0 {
					return nil, clierr.New("no_trace", "任务 "+jid+" 没有对局记录", "只有对局才有复盘页")
				}
				return pybridge.CLI("run", "review", "--job", jid) // 复盘页还是 Python 的 tools/viewer（阶段 3 迁）
			},
		},
	)
}

// events：--follow 时每条事件写一行 {"event": …}，任务结束后返回 {job, state, next}。
func events(ctx *cli.Ctx, a cli.Args) (any, error) {
	since, _ := a.Int("since")
	body := jsonx.NewObject().Set("job", nullable(a.Str("job"))).Set("since", since)
	if !a.Bool("follow") {
		return service.Data("events", body)
	}
	for {
		d, err := service.Data("events", body.Set("since", since))
		if err != nil {
			return nil, err
		}
		out, _ := d.(*jsonx.Object)
		for _, ev := range jsonx.List(out, "events") {
			line, err := jsonx.Marshal(jsonx.NewObject().Set("event", ev))
			if err != nil {
				return nil, err
			}
			if _, err := fmt.Fprintf(ctx.Stdout, "%s\n", line); err != nil {
				return nil, err
			}
			if n, ok := jsonx.Int(ev.(*jsonx.Object), "seq"); ok {
				since = n + 1
			}
		}
		job, _ := out.Get("job")
		body.Set("job", job)
		state := jsonx.Str(out, "state")
		if !contains(service.Live, state) && state != "stopping" {
			return jsonx.NewObject().Set("job", job).Set("state", state).Set("next", since), nil
		}
		time.Sleep(followPoll)
	}
}
