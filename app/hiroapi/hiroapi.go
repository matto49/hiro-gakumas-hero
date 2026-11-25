// Package hiroapi 是工具层（hiro 命令行）信封的类型：界面的子包和 main 都用它。
package hiroapi

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

// CliError 是命令失败时信封里的 error：code 给程序判断，message / hint 是写给人看的白话。
type CliError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Hint    string `json:"hint,omitempty"`
}

// Pending 是会动游戏的命令返回的待确认：用户点了确认卡才 confirm。
type Pending struct {
	ID        string         `json:"id"`
	Command   string         `json:"command"`
	Args      map[string]any `json:"args,omitempty"`   // 命令的参数（run start 的 kind / idol / …），确认卡按它画
	Access    string         `json:"access,omitempty"` // 操作类别（read / daily / blank / write / overwrite / all），按权限档决定要不要确认
	Summary   string         `json:"summary"`
	ExpiresAt float64        `json:"expires_at"`
}

// Map 是存进对话卡片（JSON）里的样子。
func (p Pending) Map() map[string]any {
	m := map[string]any{"id": p.ID, "command": p.Command, "summary": p.Summary, "expires_at": p.ExpiresAt}
	if len(p.Args) > 0 {
		m["args"] = p.Args
	}
	if p.Access != "" {
		m["access"] = p.Access
	}
	return m
}

// ArgvOf 是按待确认的命令名和参数重新拼出的命令行（合并确认卡排队的下一项要重新发起）：
// run start 的 kind 是位置参数，其余按 --参数名（下划线换成连字符），bool 只在 true 时写开关，空值不写。
func ArgvOf(command string, args map[string]any) []string {
	argv := strings.Fields(command)
	if k, _ := args["kind"].(string); k != "" {
		argv = append(argv, k)
	}
	keys := make([]string, 0, len(args))
	for k := range args {
		if k != "kind" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	for _, k := range keys {
		flag := "--" + strings.ReplaceAll(k, "_", "-")
		switch v := args[k].(type) {
		case nil:
		case bool:
			if v {
				argv = append(argv, flag)
			}
		case float64:
			argv = append(argv, flag, strconv.FormatFloat(v, 'f', -1, 64))
		case string:
			if v != "" {
				argv = append(argv, flag, v)
			}
		default:
			argv = append(argv, flag, fmt.Sprint(v))
		}
	}
	return argv
}

// PendingFromMap 从对话卡片里读回待确认；没有 id 时 ok=false。
func PendingFromMap(m map[string]any) (Pending, bool) {
	if m == nil {
		return Pending{}, false
	}
	p := Pending{}
	p.ID, _ = m["id"].(string)
	p.Command, _ = m["command"].(string)
	p.Summary, _ = m["summary"].(string)
	p.Args, _ = m["args"].(map[string]any)
	p.Access, _ = m["access"].(string)
	switch v := m["expires_at"].(type) {
	case float64:
		p.ExpiresAt = v
	case int:
		p.ExpiresAt = float64(v)
	case int64:
		p.ExpiresAt = float64(v)
	}
	return p, p.ID != ""
}

// Result 是一次命令的结果：信封原样带回，再加上退出码和解析出的待确认。
type Result struct {
	OK      bool            `json:"ok"`
	Data    json.RawMessage `json:"data,omitempty"`
	Error   *CliError       `json:"error,omitempty"`
	Pending *Pending        `json:"pending,omitempty"`
	Exit    int             `json:"exit"`
}

// PendingExit 是待确认的退出码（hiro.cli.main.PENDING_EXIT）。
const PendingExit = 3

// Caller 调一条 hiro 命令（Hiro.Call 的形状），测试里换成假的。
type Caller func(ctx context.Context, args []string) Result

// Decode 把成功结果的 data 解到 v；失败时返回信封里的错误。
func (r Result) Decode(v any) error {
	if !r.OK {
		if r.Error != nil {
			return r.Error
		}
		return &CliError{Code: "error", Message: "出错了"}
	}
	if len(r.Data) == 0 {
		return nil
	}
	return json.Unmarshal(r.Data, v)
}

func (e *CliError) Error() string { return e.Message }
