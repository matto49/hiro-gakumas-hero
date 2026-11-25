package common

import (
	"fmt"
	"sort"
	"strings"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
)

// RunArgs 是一条「hiro run start <kind> …」解析出来的东西。
type RunArgs struct {
	Kind  string
	Flags map[string]string
}

// ParseRunStart 解析待确认里的命令文字；不是 run start 就 ok=false。
func ParseRunStart(command string) (RunArgs, bool) {
	f := strings.Fields(command)
	i := 0
	for i+1 < len(f) && !(f[i] == "run" && f[i+1] == "start") {
		i++
	}
	if i+2 >= len(f) {
		return RunArgs{}, false
	}
	r := RunArgs{Kind: f[i+2], Flags: map[string]string{}}
	for j := i + 3; j < len(f); j++ {
		if strings.HasPrefix(f[j], "--") {
			k := strings.TrimPrefix(f[j], "--")
			if j+1 < len(f) && !strings.HasPrefix(f[j+1], "--") {
				r.Flags[k] = f[j+1]
				j++
			} else {
				r.Flags[k] = "true"
			}
		}
	}
	return r, true
}

// StartArgv 是重新发起这个任务的「run start」命令行：Job.Params 里空的、false 的不带，其余按 --名字 值（G1-F2「再试一次」）。
func StartArgv(kind string, params map[string]any) []string {
	argv := []string{"run", "start", kind}
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		flag := "--" + strings.ReplaceAll(k, "_", "-")
		switch x := params[k].(type) {
		case nil:
		case bool:
			if x {
				argv = append(argv, flag)
			}
		case float64:
			argv = append(argv, flag, fmt.Sprintf("%g", x))
		case string:
			if x != "" {
				argv = append(argv, flag, x)
			}
		default:
			argv = append(argv, flag, fmt.Sprint(x))
		}
	}
	return argv
}

// RunArgsOf 是待确认对应的 run start：优先用 CLI 带回来的 args（command 只是命令名「run start」），
// 没有 args 的旧卡再从命令文字解析。不是 run start 或认不出种类时 ok=false。
func RunArgsOf(p hiroapi.Pending) (RunArgs, bool) {
	if kind, _ := p.Args["kind"].(string); kind != "" {
		r := RunArgs{Kind: kind, Flags: map[string]string{}}
		for k, v := range p.Args {
			if k == "kind" {
				continue
			}
			switch x := v.(type) {
			case nil:
			case bool:
				if x {
					r.Flags[strings.ReplaceAll(k, "_", "-")] = "true"
				}
			case float64:
				r.Flags[strings.ReplaceAll(k, "_", "-")] = fmt.Sprintf("%g", x)
			case string:
				if x != "" {
					r.Flags[strings.ReplaceAll(k, "_", "-")] = x
				}
			default:
				r.Flags[strings.ReplaceAll(k, "_", "-")] = fmt.Sprint(x)
			}
		}
		return r, true
	}
	return ParseRunStart(p.Command)
}
