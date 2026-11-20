// Package plan 是 `hiro plan …`：开局编成方案。同 Python 版 hiro.cli.cmd_plan。
//
// 方案文件在 HIRO_HOME/formation-plan.json；开局任务和写编成任务都读它（HIRO_FORMATION_PLAN）。
// 出方案要用社区规则（Python 的 hiro.brain.formation_plan），阶段 4 迁完之前经 pybridge 交给 Python 算；看方案是 Go 自己读。
package plan

import (
	"os"
	"path/filepath"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
	"github.com/matto49/hiro-gakumas-hero/go/internal/store"
)

// FileName 是方案文件名。
const FileName = "formation-plan.json"

// Path 是方案文件路径。
func Path() string { return filepath.Join(home.Dir(), FileName) }

func get(o *jsonx.Object, k string) any { v, _ := o.Get(k); return v }

func objOrEmpty(o *jsonx.Object, k string) *jsonx.Object {
	if v := jsonx.Obj(o, k); v != nil {
		return v
	}
	return jsonx.NewObject()
}

func listOrEmpty(o *jsonx.Object, k string) []any {
	if v := jsonx.List(o, k); v != nil {
		return v
	}
	return []any{}
}

// Summarize 方案 → 界面编成卡和 agent 用的摘要（字段和顺序同 Python summarize）。
func Summarize(plan *jsonx.Object) *jsonx.Object {
	info, sup, mem := objOrEmpty(plan, "idol"), objOrEmpty(plan, "support"), objOrEmpty(plan, "memory")
	own := []any{}
	for _, v := range listOrEmpty(sup, "own") {
		o, _ := v.(*jsonx.Object)
		why := listOrEmpty(o, "why")
		if len(why) > 4 {
			why = why[:4]
		}
		own = append(own, jsonx.NewObject().Set("id", get(o, "support_card_id")).Set("name", get(o, "name")).Set("attr", get(o, "attr")).
			Set("level", get(o, "level")).Set("value", get(o, "value")).Set("why", why))
	}
	var rental any
	r, rule := jsonx.Obj(sup, "rental"), jsonx.Obj(sup, "rental_rule")
	if jsonx.Truthy(r) || jsonx.Truthy(rule) {
		attr := get(r, "attr")
		if !jsonx.Truthy(attr) {
			attr = get(rule, "attr")
		}
		rental = jsonx.NewObject().Set("name", get(r, "name")).Set("attr", attr).Set("value", get(r, "value")).
			Set("estimated", jsonx.Truthy(get(sup, "rental_estimated")))
	}
	chosen := []any{}
	for _, v := range listOrEmpty(mem, "chosen") {
		c, _ := v.(*jsonx.Object)
		upgrade := get(c, "upgrade")
		if !jsonx.Truthy(upgrade) {
			upgrade = 0
		}
		chosen = append(chosen, jsonx.NewObject().Set("id", get(c, "id")).Set("card_id", get(c, "card_id")).Set("name", get(c, "name")).
			Set("upgrade", upgrade).Set("timing", get(c, "timing")).Set("slot", get(c, "slot")).Set("role", get(c, "role")))
	}
	return jsonx.NewObject().
		Set("created", get(plan, "created")).
		Set("idol", jsonx.NewObject().Set("id", get(info, "idol_card_id")).Set("name", get(info, "name")).Set("plan", get(info, "plan")).
			Set("profile", get(plan, "profile"))).
		Set("main_attr", get(plan, "main_attr")).
		Set("support", jsonx.NewObject().Set("set", get(sup, "set")).Set("error", get(sup, "error")).Set("value", get(sup, "value")).
			Set("counts", get(sup, "counts")).Set("main_attr_why", get(sup, "main_attr_why")).Set("own", own).Set("rental", rental)).
		Set("memory", jsonx.NewObject().Set("set", get(mem, "set")).Set("error", get(mem, "error")).Set("cards", listOrEmpty(mem, "cards")).
			Set("chosen", chosen)).
		Set("notes", listOrEmpty(plan, "notes"))
}

// Register 登记 `hiro plan …` 命令。
func Register(r *cli.Registry) {
	formation := &cli.Command{
		Name: "plan formation", Risk: cli.Read,
		Help: "按社区规则出开局编成方案：用信息库里最新的支援卡和回忆库存选支援卡 5 张 + 租借 1 张、回忆 4 份，写成方案文件" +
			"（开局时按它核对，写编成任务按它写进游戏）。只计算，不碰游戏。",
		Args: []cli.Arg{
			{Name: "--idol", Help: "偶像卡编号（i_card-… 或 i_card-skin-…）", Required: true},
			{Name: "--support-set", Help: "方案存进游戏里第几号支援卡编成（1–20）", Type: cli.Int},
			{Name: "--memory-set", Help: "方案存进第几号メモリー编成（1–20）", Type: cli.Int},
			{Name: "--main-attr", Help: "默认主属性（不给按偶像成长率）", Choices: []string{"Vo", "Da", "Vi"}},
			{Name: "--fixed-main", Help: "只用 --main-attr，不比较逆练", Type: cli.Bool},
		},
	}
	formation.Handler = func(_ *cli.Ctx, a cli.Args) (any, error) {
		st, err := store.OpenHome()
		if err != nil {
			return nil, err
		}
		sup, err1 := st.LatestRaw("supports")
		mem, err2 := st.LatestRaw("memories")
		_ = st.Close()
		if err1 != nil || err2 != nil {
			return nil, clierr.New("store", "信息库读不了", "重试；一直这样请反馈")
		}
		if sup == nil && mem == nil {
			return nil, clierr.New("no_inventory", "还没有支援卡和回忆的库存", "先扫描支援卡和回忆（hiro run start scan-supports / scan-memories）")
		}
		return pybridge.CLI(formation.Argv(a)...) // 社区规则还在 Python（阶段 4 迁）
	}
	r.MustRegister(
		formation,
		&cli.Command{
			Name: "plan show", Risk: cli.Read,
			Help: "看现在的开局编成方案（上一次 hiro plan formation 出的）。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) {
				p := Path()
				data, err := os.ReadFile(p)
				var plan *jsonx.Object
				if err == nil {
					plan, err = jsonx.DecodeObject(data)
				}
				if err != nil {
					return nil, clierr.New("no_plan", "还没有编成方案", "先出一份：hiro plan formation --idol <偶像卡编号>")
				}
				return Summarize(plan).Set("path", p), nil
			},
		},
	)
}
