package service

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// selftestJPEG 是自检写的一张小截图（9×16 的 JPEG，同 Python 版）。
const selftestJPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAQAAkDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDkaKKKko//2Q=="

// Selftest 是自检任务：打印模型设置（Key 只说有没有）、写一步 trace 和一张截图，然后每 0.2 秒打印一行，认暂停旗标。
// args：[秒数, trace 名]。返回退出码。
func Selftest(args []string) int {
	if len(args) < 2 {
		fmt.Fprintln(os.Stderr, "用法：hiro __selftest <秒数> <trace 名>")
		return 2
	}
	seconds, err := strconv.ParseFloat(args[0], 64)
	if err != nil {
		fmt.Fprintln(os.Stderr, "秒数不对：", args[0])
		return 2
	}
	key := "unset"
	if os.Getenv("OPENAI_API_KEY") != "" {
		key = "set"
	}
	fmt.Printf("llm=%s base=%s key=%s\n", os.Getenv("HIRO_LLM"), os.Getenv("OPENAI_BASE_URL"), key)
	trace := filepath.Join("traces", filepath.Base(args[1]))
	if err := os.MkdirAll(filepath.Join(trace, "frames"), 0o750); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	jpg, _ := base64.StdEncoding.DecodeString(selftestJPEG)
	_ = os.WriteFile(filepath.Join(trace, "frames", "00001.jpg"), jpg, 0o600)
	step := jsonx.NewObject().Set("seq", 1).
		Set("observation", jsonx.NewObject().Set("page", jsonx.NewObject().Set("name", "SELFTEST"))).
		Set("decision", jsonx.NewObject().Set("action", jsonx.NewObject().Set("kind", "Wait")).Set("reason", "自检：不碰游戏").
			Set("source", "rule").Set("decider", "selftest")).
		Set("notes", []any{})
	line, _ := jsonx.Marshal(step)
	if f, err := os.OpenFile(filepath.Join(trace, "steps.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600); err == nil {
		_, _ = f.Write(append(line, '\n'))
		_ = f.Close()
	}
	n := max(1, int(seconds*5))
	flag := os.Getenv("HIRO_PAUSE_FLAG")
	for i := 0; i < n; {
		if flag != "" {
			if _, err := os.Stat(flag); err == nil {
				time.Sleep(100 * time.Millisecond)
				continue
			}
		}
		i++
		fmt.Printf("selftest %d\n", i)
		time.Sleep(200 * time.Millisecond)
	}
	return 0
}
