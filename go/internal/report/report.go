// Package report 是局后的成绩摘要。同 Python 版 hiro.cli.report：直接读 trace 的 steps.jsonl 原始 JSON
// （不建契约对象：不同版本的 trace 字段会多出来）。
//
// 每场考试的分数要用复盘工具（Python 的 tools/viewer/build_exam_view.py）切分考试，阶段 3 迁完之前经 pybridge 取。
package report

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

var jobPattern = regexp.MustCompile(`^[A-Za-z0-9_\-]{1,64}$`)

// CheckJob 检查任务编号（会拼进路径）。
func CheckJob(id string) error {
	if !jobPattern.MatchString(id) {
		return clierr.Usage("bad_args", "任务编号不对："+id, "用 hiro run status 看任务编号")
	}
	return nil
}

// TracesDir 是 HIRO_HOME/work/traces。
func TracesDir() string { return filepath.Join(home.Dir(), "work", "traces") }

// Segments 是一个任务的 trace：<编号> 和续跑段 <编号>-rN（按 N 排），只要有 steps.jsonl 的。
func Segments(traces, base string) []string {
	segs := []string{base}
	matches, _ := filepath.Glob(filepath.Join(traces, base+"-r*"))
	var resumes []string
	for _, m := range matches {
		resumes = append(resumes, filepath.Base(m))
	}
	n := func(name string) int {
		i, err := strconv.Atoi(name[strings.LastIndex(name, "-r")+2:])
		if err != nil {
			return 0
		}
		return i
	}
	sort.SliceStable(resumes, func(i, j int) bool { return n(resumes[i]) < n(resumes[j]) })
	segs = append(segs, resumes...)
	var out []string
	for _, s := range segs {
		if _, err := os.Stat(filepath.Join(traces, s, "steps.jsonl")); err == nil {
			out = append(out, s)
		}
	}
	return out
}

// examScores 每场考试的最终分 [{label, score}]；读不了就空。经 Python 版 `hiro run report` 取（测试里换）。
var examScores = func(base string) []any {
	data, err := pybridge.CLI("run", "report", "--job", base)
	if err != nil {
		return []any{}
	}
	o, _ := data.(*jsonx.Object)
	if l := jsonx.List(o, "exams"); l != nil {
		return l
	}
	return []any{}
}

// ExamScores 每场考试的最终分。
func ExamScores(base string) []any { return examScores(base) }

// Summarize 一局的成绩摘要：步数、决策来源、LLM 次数、故障恢复次数、每场考试分数。
func Summarize(base string) (*jsonx.Object, error) {
	if err := CheckJob(base); err != nil {
		return nil, err
	}
	traces := TracesDir()
	segs := Segments(traces, base)
	if len(segs) == 0 {
		return nil, clierr.New("no_trace", "任务 "+base+" 没有对局记录", "只有对局（hif / hajime / nia / tower）才有记录")
	}
	steps, decisions, llm, recov := 0, 0, 0, 0
	bySource := jsonx.NewObject()
	var finalPage any
	for _, seg := range segs {
		f, err := os.Open(filepath.Join(traces, seg, "steps.jsonl"))
		if err != nil {
			continue
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 64<<20) // 一步里可能带很长的候选列表
		for sc.Scan() {
			rec, err := jsonx.DecodeObject(sc.Bytes())
			if err != nil {
				continue
			}
			steps++
			if page := jsonx.Str(jsonx.Obj(jsonx.Obj(rec, "observation"), "page"), "name"); page != "" {
				finalPage = page
			}
			if dec := jsonx.Obj(rec, "decision"); dec != nil {
				decisions++
				src := jsonx.Str(dec, "source")
				if src == "" {
					src = "?"
				}
				n, _ := jsonx.Int(bySource, src)
				bySource.Set(src, n+1)
				llm += len(jsonx.List(dec, "llm"))
			}
			for _, note := range jsonx.List(rec, "notes") {
				if s, ok := note.(string); ok && strings.HasPrefix(s, "recover:") {
					recov++
				}
			}
		}
		_ = f.Close()
	}
	segAny := make([]any, len(segs))
	for i, s := range segs {
		segAny[i] = s
	}
	return jsonx.NewObject().Set("job", base).Set("segments", segAny).Set("steps", steps).Set("decisions", decisions).
		Set("by_source", bySource).Set("llm_calls", llm).Set("recoveries", recov).Set("final_page", finalPage).
		Set("exams", examScores(base)), nil
}

// ScoreLine 是事件流里的一句话：「3 场考试：12,271 / 85,055 / 368,997」；没有分数给 ""。
func ScoreLine(exams []any) string {
	var parts []string
	for _, e := range exams {
		o, _ := e.(*jsonx.Object)
		v, _ := o.Get("score")
		n, ok := v.(json.Number)
		if !ok {
			continue
		}
		i, err := n.Int64()
		if err != nil {
			f, ferr := n.Float64()
			if ferr != nil {
				continue
			}
			i = int64(f)
		}
		parts = append(parts, thousands(i))
	}
	if len(parts) == 0 {
		return ""
	}
	return fmt.Sprintf("%d 场考试：%s", len(parts), strings.Join(parts, " / "))
}

func thousands(n int64) string {
	s := strconv.FormatInt(n, 10)
	neg := strings.HasPrefix(s, "-")
	s = strings.TrimPrefix(s, "-")
	var b strings.Builder
	for i, c := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(c)
	}
	if neg {
		return "-" + b.String()
	}
	return b.String()
}
