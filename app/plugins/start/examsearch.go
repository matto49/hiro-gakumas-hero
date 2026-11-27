package start

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// 开局页「考试出牌」（11-27 用户）：按场次选快速（UINet，每步不到 1 秒）还是搜索（GNRPA，每步约 5 秒，H.I.F 本战约 15 秒）。
// 发起时总是带 --exam-search（选了搜索的场次，逗号分隔；都快速写 none），工具层按家族变成 HIRO_EXAM_SEARCH_*。
// 每种模式上次怎么选的记在 HIRO_HOME/start-prefs.json，下次打开照旧。

type examRound struct {
	key, name string
	def       bool // 工具层不传参数时的缺省（初全搜、N.I.A 不搜、H.I.F 选拔不搜、本战都搜）
}

var examRounds = map[string][]examRound{
	"hajime": {{"mid1", "中間試験", true}, {"final", "最終試験", true}, {"lesson", "练习课", true}},
	"nia":    {{"mid1", "第 1 场试镜", false}, {"mid2", "第 2 场试镜", false}, {"final", "最终试镜", false}},
	"hif-q":  {{"q1", "选拔第 1 场", false}, {"q2", "选拔第 2 场", false}, {"q3", "选拔第 3 场", false}},
	"hif-m":  {{"r1", "本战 R1", true}, {"r2", "本战 R2", true}},
}

// examGroups 是这个模式（H.I.F 再按选拔 / 本战）对应的场次组；没有考试选择的模式返回空。
// H.I.F「接着打」不知道进行中的是选拔还是本战（页签可能停在另一个），两组都带上（只带一组时，另一组会被工具层写成全快速）。
func examGroups(mode string, hifMain int, resume bool) []string {
	switch mode {
	case "hajime", "nia":
		return []string{mode}
	case "hif":
		if resume {
			return []string{"hif-q", "hif-m"}
		}
		return []string{[]string{"hif-q", "hif-m"}[min(max(hifMain, 0), 1)]}
	}
	return nil
}

type startPrefs struct {
	ExamSearch map[string]map[string]bool `json:"exam_search,omitempty"`
}

// searchState 是「考试出牌」的选择（按组、按场次）和存盘位置。
type searchState struct {
	loaded bool
	path   string
	picks  map[string]map[string]bool
}

func (ss *searchState) load(h plugin.Host) {
	if ss.loaded {
		return
	}
	ss.loaded = true
	ss.picks = map[string]map[string]bool{}
	if h == nil || h.Home() == "" {
		return
	}
	ss.path = filepath.Join(h.Home(), "start-prefs.json")
	var p startPrefs
	if b, err := os.ReadFile(ss.path); err == nil && json.Unmarshal(b, &p) == nil && p.ExamSearch != nil {
		ss.picks = p.ExamSearch
	}
}

func (ss *searchState) save() {
	if ss.path == "" {
		return
	}
	b, err := json.MarshalIndent(startPrefs{ExamSearch: ss.picks}, "", "  ")
	if err == nil {
		_ = os.WriteFile(ss.path, b, 0o644)
	}
}

// on 是这一场选没选搜索（没选过按缺省）。
func (ss *searchState) on(group string, r examRound) bool {
	if g, ok := ss.picks[group]; ok {
		if v, ok := g[r.key]; ok {
			return v
		}
	}
	return r.def
}

func (ss *searchState) set(group, key string, v bool) {
	if ss.picks == nil {
		ss.picks = map[string]map[string]bool{}
	}
	if ss.picks[group] == nil {
		ss.picks[group] = map[string]bool{}
	}
	ss.picks[group][key] = v
}

// args 是 --exam-search 参数（这个模式没有考试选择时为空）。
func (ss *searchState) args(groups []string) []string {
	if len(groups) == 0 {
		return nil
	}
	var on []string
	for _, group := range groups {
		for _, r := range examRounds[group] {
			if ss.on(group, r) {
				on = append(on, r.key)
			}
		}
	}
	if len(on) == 0 {
		return []string{"--exam-search", "none"}
	}
	return []string{"--exam-search", strings.Join(on, ",")}
}

// draw 画「考试出牌」：每场一行「快速 / 搜索」。
func (ss *searchState) draw(c *ui.Context, h plugin.Host, groups []string) {
	if len(groups) == 0 {
		return
	}
	ss.load(h)
	kit.Section(c, "考试出牌")
	for _, group := range groups {
		ss.drawGroup(c, group)
	}
	note := "快速：每步不到 1 秒。搜索：更准，每步约 5 秒。搜索缺东西或出错时那一步自动用快速。"
	for _, group := range groups {
		if group == "hif-m" {
			note = "快速：每步不到 1 秒。搜索：更准，每步约 5 秒（H.I.F 本战约 15 秒，多占约 600 MB 内存，内存紧时模拟器可能被系统关掉）。搜索缺东西或出错时那一步自动用快速。"
		}
		if group == "nia" {
			note += "N.I.A 的搜索还在验证，缺省都是快速。"
		}
	}
	kit.Note(c, note)
}

func (ss *searchState) drawGroup(c *ui.Context, group string) {
	for _, r := range examRounds[group] {
		i := 0
		if ss.on(group, r) {
			i = 1
		}
		ui.Row(c).Key("exam-search-" + group + "-" + r.key).Gap(10).AlignItems(ui.Center).Children(func() {
			ui.Text(c, r.name).FontSize(kit.FSmall).TextColor(kit.Ink2).Width(96)
			ui.Segmented(c, &i, "快速", "搜索")
		})
		if (i == 1) != ss.on(group, r) {
			ss.set(group, r.key, i == 1)
			ss.save()
		}
	}
}

// examSearchRow 是确认卡上「考试出牌」那一行：哪几场搜索、哪几场快速。没带 --exam-search 时 ok=false。
func examSearchRow(kind string, flags map[string]string) ([2]string, bool) {
	v, has := flags["exam-search"]
	if !has {
		return [2]string{}, false
	}
	var rounds []examRound
	switch {
	case kind == "hif" && flags["command"] == "resume":
		rounds = append(append(rounds, examRounds["hif-q"]...), examRounds["hif-m"]...)
	case kind == "hif" && flags["mode"] == "main":
		rounds = examRounds["hif-m"]
	case kind == "hif":
		rounds = examRounds["hif-q"]
	default:
		rounds = examRounds[kind]
	}
	if len(rounds) == 0 {
		return [2]string{}, false
	}
	on := map[string]bool{}
	for _, k := range strings.Split(v, ",") {
		on[strings.TrimSpace(k)] = true
	}
	var search, fast []string
	for _, r := range rounds {
		if on[r.key] {
			search = append(search, r.name)
		} else {
			fast = append(fast, r.name)
		}
	}
	var parts []string
	if len(search) > 0 {
		parts = append(parts, "搜索："+strings.Join(search, "、"))
	}
	if len(fast) > 0 {
		parts = append(parts, "快速："+strings.Join(fast, "、"))
	}
	return [2]string{"考试出牌", strings.Join(parts, "；")}, true
}
