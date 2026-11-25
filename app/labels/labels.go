// Package labels 是界面上的叫法：页面名、动作名、任务种类、状态、谁定的、属性（原 src/lib/labels.ts）。
package labels

import (
	"fmt"
	"regexp"
	"strings"
	"time"
)

var states = map[string]string{
	"running":  "游玩中",
	"paused":   "已暂停",
	"takeover": "你在接手",
	"stopping": "正在停下",
	"done":     "完成",
	"failed":   "出错停下",
	"stopped":  "已停止",
	"lost":     "中断（程序重启）",
}

var kinds = map[string]string{
	"hif":             "H.I.F",
	"hajime":          "初",
	"nia":             "N.I.A",
	"tower":           "偶像之路",
	"daily":           "日常",
	"main-task":       "初星課題",
	"story":           "看初星コミュ",
	"contest-unit":    "竞赛编队",
	"scan-idols":      "扫描偶像卡",
	"scan-supports":   "扫描支援卡",
	"scan-memories":   "扫描回忆",
	"scan-account":    "读首页数值",
	"check-settings":  "检查游戏设置",
	"write-formation": "写编成",
	"fit-window":      "调窗口大小",
	"selftest":        "自检",
}

var pages = map[string]string{
	"HOME":                  "首页",
	"SELFTEST":              "自检",
	"ACTION_SELECT":         "选行动",
	"HIF_EXAM":              "考试中",
	"HIF_EXAM_BRIEFING":     "考试说明",
	"HIF_EXAM_RESULT":       "考试结果",
	"HIF_FINALS_START":      "本战开始",
	"HIF_FINALS_RANKING":    "本战排名",
	"HIF_ROUND_INTERVAL":    "轮间",
	"SELECT_CARD":           "选技能卡",
	"SELECT_DRINK":          "选饮料",
	"SELECT_PITEM":          "选 P 道具",
	"PRODUCE_START_CONFIRM": "开局确认",
	"PRODUCE_END":           "培育结束",
	"PRODUCE_LOG":           "培育记录",
	"CONSULT":               "相談",
	"STUDY":                 "授業",
	"OUTING":                "外出",
	"EVENT_CHOICE":          "事件选项",
	"DECK_LIST":             "牌组",
	"SKILL_CARD_REMOVAL":    "删卡",
	"SKILL_CARD_ENHANCE":    "强化卡",
	"SKILL_CARD_CHANGE_1":   "换卡",
	"SKILL_CARD_CHANGE_2":   "换卡",
	"PDRINK_MAX":            "饮料满了",
	"PDRINK_MAX_CONFIRM":    "饮料满了",
	"SPECIAL_GUIDANCE":      "特別指導",
	"PITEM_CUSTOMIZE":       "P 道具定制",
	"PITEM_CUSTOMIZE_DONE":  "P 道具定制",
	"CUSTOMIZE_RESULT":      "定制结果",
	"UPGRADE_RESULT":        "强化结果",
	"DELETE_RESULT":         "删卡结果",
	"DIALOG":                "弹窗",
	"ACQUIRE":               "获得",
	"STAR_GAIN":             "获得星",
	"MENU":                  "菜单",
	"TRANSITION":            "切换中",
	"GIFT":                  "礼物",
	"commu":                 "剧情",
	"unknown":               "没认出的画面",
	"TOWER_LAYERS":          "偶像之路 · 选层",
	"TOWER_FORMATION":       "偶像之路 · 编成",
}

var actions = map[string]string{
	"ProduceNext":         "选行动",
	"ActivateEffect":      "选取",
	"ExcludeCard":         "不拿",
	"Reroll":              "刷新候选",
	"ShopBuy":             "买",
	"ShopUpgradeCard":     "强化",
	"ShopEnd":             "离开商店",
	"EventSelect":         "选选项",
	"PresentReceive":      "领取",
	"IntervalBuy":         "轮间购买",
	"IntervalUpgradeCard": "轮间强化",
	"IntervalChangeCard":  "轮间换卡",
	"IntervalEnd":         "结束轮间",
	"CustomizeCard":       "定制",
	"CustomizeEnd":        "结束定制",
	"SelectFormation":     "选编成",
	"ChangeCard":          "换卡",
	"RemoveCard":          "删卡",
	"EnhanceCard":         "强化卡",
	"Dialog":              "处理弹窗",
	"SelectQualifyMemory": "选回忆",
	"DiscardDrink":        "丢饮料",
	"PlayCard":            "出牌",
	"UseDrink":            "喝饮料",
	"Skip":                "跳过",
	"Continue":            "继续",
	"Wait":                "等待",
	"Recover":             "恢复",
	"Noop":                "不动",
	"Lesson":              "上课",
	"Pick":                "选择",
}

var timings = map[string]string{
	"start":                 "开局",
	"mid":                   "中间",
	"final":                 "最终",
	"HOME":                  "首页",
	"SELFTEST":              "自检",
	"ACTION_SELECT":         "选行动",
	"HIF_EXAM":              "考试中",
	"HIF_EXAM_BRIEFING":     "考试说明",
	"HIF_EXAM_RESULT":       "考试结果",
	"HIF_FINALS_START":      "本战开始",
	"HIF_FINALS_RANKING":    "本战排名",
	"HIF_ROUND_INTERVAL":    "轮间",
	"SELECT_CARD":           "选技能卡",
	"SELECT_DRINK":          "选饮料",
	"SELECT_PITEM":          "选 P 道具",
	"PRODUCE_START_CONFIRM": "开局确认",
	"PRODUCE_END":           "培育结束",
	"PRODUCE_LOG":           "培育记录",
	"CONSULT":               "相談",
	"STUDY":                 "授業",
	"OUTING":                "外出",
	"EVENT_CHOICE":          "事件选项",
	"DECK_LIST":             "牌组",
	"SKILL_CARD_REMOVAL":    "删卡",
	"SKILL_CARD_ENHANCE":    "强化卡",
	"SKILL_CARD_CHANGE_1":   "换卡",
	"SKILL_CARD_CHANGE_2":   "换卡",
	"PDRINK_MAX":            "饮料满了",
	"PDRINK_MAX_CONFIRM":    "饮料满了",
	"SPECIAL_GUIDANCE":      "特別指導",
	"PITEM_CUSTOMIZE":       "P 道具定制",
	"PITEM_CUSTOMIZE_DONE":  "P 道具定制",
	"CUSTOMIZE_RESULT":      "定制结果",
	"UPGRADE_RESULT":        "强化结果",
	"DELETE_RESULT":         "删卡结果",
	"DIALOG":                "弹窗",
	"ACQUIRE":               "获得",
	"STAR_GAIN":             "获得星",
	"MENU":                  "菜单",
	"TRANSITION":            "切换中",
	"GIFT":                  "礼物",
	"commu":                 "剧情",
	"unknown":               "没认出的画面",
	"TOWER_LAYERS":          "偶像之路 · 选层",
	"TOWER_FORMATION":       "偶像之路 · 编成",
}

var attrs = map[string]string{
	"Vo": "声乐", "Da": "舞蹈", "Vi": "视觉",
	"vocal": "声乐", "dance": "舞蹈", "visual": "视觉", "assist": "辅助",
}

func pick(m map[string]string, k, empty string) string {
	if k == "" {
		return empty
	}
	if v, ok := m[k]; ok {
		return v
	}
	return k
}

// Page 是页面名（trace 的场景名）的中文；没收录的原样返回，空的是「—」。
func Page(p string) string { return pick(pages, p, "—") }

// Action 是动作名的中文。
func Action(a string) string { return pick(actions, a, "") }

// Kind 是任务种类（hajime、hif、scan-supports…）的中文。
func Kind(k string) string { return pick(kinds, k, "") }

// State 是任务状态的中文。
func State(s string) string { return pick(states, s, "") }

// Timing 是回忆时机的中文。
func Timing(s string) string { return pick(timings, s, "") }

// Attr 是属性（Vo / vocal…）的中文。
func Attr(a string) string { return pick(attrs, a, "") }

var (
	reLLM    = regexp.MustCompile(`llm|claude|codex|openai|deepseek`)
	reRescue = regexp.MustCompile(`rescue|fallback|兜底`)
	reAlgo   = regexp.MustCompile(`bench|search|gnrpa|uinet|sim|algo|engine`)
	reRule   = regexp.MustCompile(`rule|community|default|plan`)
)

// IsPluginSource 是这一步是不是插件定的（事件 source 为 plugin:<id@版本>，见 hiro.cli.jobs.step_event）。
func IsPluginSource(s string) bool { return strings.HasPrefix(s, "plugin:") }

// Source 是每一步谁定的：规则、算法、LLM、兜底、插件 <id>；空的是「自动」。
func Source(s string) string {
	if IsPluginSource(s) {
		id, _, _ := strings.Cut(strings.TrimPrefix(s, "plugin:"), "@")
		return strings.TrimSpace("插件 " + id)
	}
	l := strings.ToLower(s)
	switch {
	case l == "":
		return "自动"
	case reLLM.MatchString(l):
		return "LLM"
	case reRescue.MatchString(l):
		return "兜底"
	case reAlgo.MatchString(l):
		return "算法"
	case reRule.MatchString(l):
		return "规则"
	}
	return s
}

var logPrefix = regexp.MustCompile(`^\[\d{4}-\d\d-\d\d [\d:,.]+\]\s*\[[A-Z]+\]\s*\[[^\]]+\]\s*`)

// LogText 去掉日志行的「[时间] [级别] [模块]」前缀。
func LogText(s string) string { return logPrefix.ReplaceAllString(s, "") }

// Elapsed 是 start 到 now（秒）过了多久。
func Elapsed(start, now float64) string {
	m := int(max(0, now-start) / 60)
	switch {
	case m < 1:
		return "不到 1 分钟"
	case m < 60:
		return fmt.Sprintf("%d 分钟", m)
	}
	return fmt.Sprintf("%d 小时 %d 分", m/60, m%60)
}

// Clock 是秒级时间戳的「时:分」（本地时区）。
func Clock(t float64) string { return time.Unix(int64(t), 0).Format("15:04") }

var tools = map[string]string{
	"info_summary": "看了账号概况", "info_supports": "查了支援卡", "info_memories": "查了回忆", "info_idols": "查了偶像卡",
	"info_affection": "查了好感度", "info_formations": "查了编成位", "info_game_settings": "查了游戏设置",
	"catalog_search": "搜了资料库", "catalog_get": "查了资料库", "catalog_supports": "查了支援卡资料", "catalog_sync": "同步了资料库",
	"plan_formation": "出了编成方案", "plan_show": "看了编成方案", "write_formation": "准备写编成", "scan_formations": "准备读编成位",
	"run_start": "发起开局", "run_status": "看了任务状态", "run_events": "看了对局步骤", "doctor": "检查了环境", "tools": "看了能用的工具",
}

// Tool 是 agent 工具名（info_idols…）给玩家看的说法；插件的工具统称「用了插件工具」。
func Tool(name string) string {
	if v, ok := tools[name]; ok {
		return v
	}
	if strings.HasPrefix(name, "plugin_") {
		return "用了插件工具"
	}
	return name
}

var (
	reTag      = regexp.MustCompile(`\s*\[[^\[\]]*\]`)
	reGuideKey = regexp.MustCompile(`攻略 [A-Za-z0-9_.\-]+、?`)
	reAlgoHead = regexp.MustCompile(`^[A-Za-z]+（[^）]*）：`)
	rePredict  = regexp.MustCompile(`；?\s*终局分预测中位\s*([\d,]+)`)
)

// Reason 把每一步的理由改成玩家看得懂的话（11-25 review）：去掉规则标签 [g8-PRO]、攻略键名、
// 算法内部名「ExamGnrpa（画面读数）：」，「终局分预测中位 N」写成「预计终局 N 分」。
func Reason(s string) string {
	s = reTag.ReplaceAllString(s, "")
	s = reGuideKey.ReplaceAllString(s, "")
	algo := reAlgoHead.MatchString(s)
	s = reAlgoHead.ReplaceAllString(s, "")
	s = rePredict.ReplaceAllString(s, " · 预计终局 $1 分")
	if algo && s != "" && !strings.HasPrefix(s, " · ") {
		s = "出 " + s
	}
	s = strings.NewReplacer("（、", "（", "、）", "）", "（）", "", "；）", "）").Replace(s)
	return strings.TrimSpace(s)
}

// Predict 是理由里的「终局分预测中位 N」（没有为空）。
func Predict(s string) string {
	if m := rePredict.FindStringSubmatch(s); m != nil {
		return m[1]
	}
	return ""
}
