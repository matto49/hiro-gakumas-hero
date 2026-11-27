package plugin

import (
	"regexp"
	"strings"
)

// Job 是 `hiro run status` 里的一个任务。
type Job struct {
	ID        string         `json:"id"`
	Kind      string         `json:"kind"`
	Params    map[string]any `json:"params"`
	State     string         `json:"state"`
	Summary   string         `json:"summary"`
	StartedAt float64        `json:"started_at"`
	EndedAt   *float64       `json:"ended_at"`
	ExitCode  *int           `json:"exit_code"`
	Reason    string         `json:"reason,omitempty"` // 出错原因（failed 时 CLI 从日志里挑的一句）
	Pausable  *bool          `json:"pausable,omitempty"`
}

// JobEvent 是 `hiro run events` 的一条事件。
type JobEvent struct {
	Seq    int      `json:"seq"`
	T      float64  `json:"t"`
	Type   string   `json:"type"`
	Text   string   `json:"text"`
	Step   *int     `json:"step,omitempty"`
	Page   string   `json:"page,omitempty"`
	Action string   `json:"action,omitempty"`
	Source string   `json:"source,omitempty"`
	Frame  string   `json:"frame,omitempty"`
	Notes  []string `json:"notes,omitempty"`
	Reason string   `json:"reason,omitempty"` // failed 事件带的出错原因
	// Exams 是 report 事件里每场考试的分数。
	Exams []ExamScore `json:"exams,omitempty"`
}

// ExamScore 是一场考试的成绩。
type ExamScore struct {
	Label string   `json:"label"`
	Score *float64 `json:"score"`
}

// LiveStates 是还活着的任务状态。
var LiveStates = map[string]bool{"running": true, "paused": true, "takeover": true, "stopping": true}

// JobView 是宿主盯着的一个任务：基本信息 + 到目前为止的事件。
type JobView struct {
	Job    *Job
	State  string
	Events []JobEvent
	// Next 是下次 run events 的 --since。
	Next int
	// Loaded 是读到过一次事件。
	Loaded bool
	// Final 是任务结束之后成功读过一次事件（最后几步、成绩都拿到了），之后不再查。
	Final bool
}

// Copy 是一份独立的拷贝（后台线程读用）。
func (v *JobView) Copy() *JobView {
	if v == nil {
		return nil
	}
	c := *v
	if v.Job != nil {
		j := *v.Job
		c.Job = &j
	}
	c.Events = append([]JobEvent(nil), v.Events...)
	return &c
}

// Live 是任务还在跑。
func (v *JobView) Live() bool { return v != nil && LiveStates[v.State] }

// Steps 是事件里的步骤（type=step）。
func (v *JobView) Steps() []JobEvent {
	var out []JobEvent
	for _, e := range v.Events {
		if e.Type == "step" {
			out = append(out, e)
		}
	}
	return out
}

// LastFrame 是最近一张画面（步骤或 frame 事件带的截图路径）。
func (v *JobView) LastFrame() string {
	for i := len(v.Events) - 1; i >= 0; i-- {
		if f := v.Events[i].Frame; f != "" {
			return f
		}
	}
	return ""
}

// Report 是最近一条 report 事件里的成绩（没有为 nil）。
func (v *JobView) Report() []ExamScore {
	for i := len(v.Events) - 1; i >= 0; i-- {
		if v.Events[i].Type == "report" && len(v.Events[i].Exams) > 0 {
			return v.Events[i].Exams
		}
	}
	return nil
}

var (
	logHead    = regexp.MustCompile(`^\[\d{4}-\d\d-\d\d [\d:,.]+\]\s*\[([A-Z]+)\]\s*\[[^\]]*\]\s*`)
	shotSuffix = regexp.MustCompile(`（截图在 [^）]*）\s*$`)
)

// FailReason 是出错停下的原因（不是 failed 时为空）：任务上记的 → failed 事件带的 → 日志里最后一条「停下：」/ ERROR
// （旧任务的 failed 事件没带原因，和 CLI 的 failure_reason 同一套规则）。
func (v *JobView) FailReason() string {
	if v == nil || v.State != "failed" {
		return ""
	}
	if v.Job != nil && v.Job.Reason != "" {
		return v.Job.Reason
	}
	stop, errLine := "", ""
	for _, e := range v.Events {
		switch {
		case e.Type == "failed" && e.Reason != "":
			return e.Reason
		case e.Type != "log":
		case strings.HasPrefix(strings.TrimSpace(e.Text), "停下："):
			stop = strings.TrimPrefix(strings.TrimSpace(e.Text), "停下：")
		default:
			if m := logHead.FindStringSubmatchIndex(e.Text); m != nil {
				if lv := e.Text[m[2]:m[3]]; lv == "ERROR" || lv == "CRITICAL" {
					errLine = e.Text[m[1]:]
				}
			}
		}
	}
	why := stop
	if why == "" {
		why = errLine
	}
	why = strings.TrimSpace(shotSuffix.ReplaceAllString(why, ""))
	if r := []rune(why); len(r) > 160 {
		why = string(r[:159]) + "…"
	}
	return why
}

// DoneText 是做完时的结果说明（结束事件的文字，如日常的「活動費：领了…；竞赛：打了 5 场…」）；只有「完成」两个字或没做完时为空。
// 11-25 Windows：日常只显示「完成」，看不出每项做了什么，像是什么都没领。
func (v *JobView) DoneText() string {
	if v == nil || v.State != "done" {
		return ""
	}
	for i := len(v.Events) - 1; i >= 0; i-- {
		if e := v.Events[i]; e.Type == "done" {
			if t := strings.TrimSpace(e.Text); t != "完成" {
				return t
			}
			return ""
		}
	}
	return ""
}
