package labels

import (
	"encoding/json"
	"os"
	"testing"
)

func TestLogTextStripsPrefix(t *testing.T) {
	cases := map[string]string{
		"[2026-11-23 19:52:24,843] [INFO] [hiro.runtime.finish] 结算画面已点完，回到首页": "结算画面已点完，回到首页",
		"[2026-11-23 19:55:35,905] [ERROR] [__main__] 租借格挑不到合适的卡，停下":          "租借格挑不到合适的卡，停下",
		"已开始": "已开始",
		"":    "",
	}
	for in, want := range cases {
		if got := LogText(in); got != want {
			t.Errorf("LogText(%q) = %q，要 %q", in, got, want)
		}
	}
}

func TestElapsed(t *testing.T) {
	for _, c := range []struct {
		d    float64
		want string
	}{{40, "不到 1 分钟"}, {12*60 + 5, "12 分钟"}, {75 * 60, "1 小时 15 分"}} {
		if got := Elapsed(1000, 1000+c.d); got != c.want {
			t.Errorf("Elapsed(+%v) = %q，要 %q", c.d, got, c.want)
		}
	}
}

func TestNames(t *testing.T) {
	if Page("HIF_EXAM") != "考试中" || Page("NEW_THING") != "NEW_THING" || Page("") != "—" {
		t.Error("页面名")
	}
	if Action("PlayCard") != "出牌" || Kind("hajime") != "初" || State("takeover") != "你在接手" {
		t.Error("动作 / 种类 / 状态")
	}
	for in, want := range map[string]string{"": "自动", "deepseek-flash": "LLM", "rescue": "兜底", "gnrpa": "算法", "community": "规则", "xyz": "xyz"} {
		if got := Source(in); got != want {
			t.Errorf("Source(%q) = %q，要 %q", in, got, want)
		}
	}
	if Attr("Vo") != "声乐" || Attr("vocal") != "声乐" || Attr("assist") != "辅助" {
		t.Error("属性")
	}
}

func TestToolNames(t *testing.T) {
	for in, want := range map[string]string{"info_idols": "查了偶像卡", "info_supports": "查了支援卡", "run_start": "发起任务", "plan_formation": "出了编成方案", "plugin_demo_echo": "用了插件工具", "weird_tool": "weird_tool"} {
		if got := Tool(in); got != want {
			t.Errorf("Tool(%q) = %q，要 %q", in, got, want)
		}
	}
}

// 2026-11-25 插件架构：插件定的步骤，事件 source 是 plugin:<id@版本>，显示成「插件 id」（不能因为 id 里有 uinet 就显示成算法）。
func TestSourcePlugin(t *testing.T) {
	for in, want := range map[string]string{"plugin:uinet@0.3": "插件 uinet", "plugin:my-llm": "插件 my-llm", "plugin:": "插件"} {
		if got := Source(in); got != want {
			t.Errorf("Source(%q) = %q，要 %q", in, got, want)
		}
	}
	if !IsPluginSource("plugin:x@1") || IsPluginSource("gnrpa") {
		t.Error("IsPluginSource")
	}
}

// 11-25 review：理由里的规则标签、攻略键名、算法内部名不给玩家看
func TestReasonDropsInternalTerms(t *testing.T) {
	cases := map[string]string{
		"选卡 オトメゴコロ（攻略 hajime.style-review.card_pick.stack、还缺的循环卡、加好印象）（牌组精简，用不上的卡不拿 [g8-PRO]；优先拿 [g8-試験]）": "选卡 オトメゴコロ（还缺的循环卡、加好印象）（牌组精简，用不上的卡不拿；优先拿）",
		"ExamGnrpa（画面读数）：目線の基本；终局分预测中位 92": "出 目線の基本 · 预计终局 92 分",
		"上课程 Vi（课程、授業优先 [g8-PRO]）":         "上课程 Vi（课程、授業优先）",
		"": "",
	}
	for in, want := range cases {
		if got := Reason(in); got != want {
			t.Errorf("Reason(%q) = %q，要 %q", in, got, want)
		}
	}
}

// N9（11-26 G1）：任务摘要后面跟着「开局前：…」说明时，标题只要第一行。
func TestTitleFirstLine(t *testing.T) {
	if got := Title("开一局「初」REGULAR（藤田琴音）\n开局前：先读一遍「初」的编成位"); got != "开一局「初」REGULAR（藤田琴音）" {
		t.Errorf("%q", got)
	}
	if got := Title("扫描偶像卡"); got != "扫描偶像卡" {
		t.Errorf("%q", got)
	}
}

// N38（G1 第 3 轮 11-26）：成绩区不露出内部考试标签。
func TestExam(t *testing.T) {
	for in, want := range map[string]string{
		"hajime-final": "最终考试", "hajime-mid1": "中间考试", "hajime-lesson-vo-hard": "追い込み Vo 课程",
		"hajime-lesson-da": "Da 课程", "hajime-lesson-vi-sp": "SP Vi 课程", "hajime-lesson": "课程",
		"nia-mid2": "第 2 次中间考试", "nia-final#2": "最终考试", "r1": "本战 R1", "tower-12": "第 12 层", "weird": "weird",
		"hajime-lesson-vo-custom": "hajime-lesson-vo-custom", "hajime-lesson-xx": "hajime-lesson-xx", "hajime-lessonx": "hajime-lessonx",
	} {
		if got := Exam(in); got != want {
			t.Errorf("Exam(%q) = %q，要 %q", in, got, want)
		}
	}
}

// 用例和复盘页（tools/viewer/build_exam_view.py 的 exam_name）共用，改一边要两边同步。
func TestExamSharedCases(t *testing.T) {
	raw, err := os.ReadFile("testdata/exam_names.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases map[string]string
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 10 {
		t.Fatalf("用例太少：%d", len(cases))
	}
	for in, want := range cases {
		if got := Exam(in); got != want {
			t.Errorf("Exam(%q) = %q，要 %q", in, got, want)
		}
	}
}
