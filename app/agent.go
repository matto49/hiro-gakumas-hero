package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

// 内置 agent（设计稿 §8）：把工具层的命令描述成工具交给模型，模型选工具，程序执行、把 JSON 结果交回模型，直到回答用户。
//
// 权限：只给 hiro tools 里 agent=true 的命令（没有 confirm、secret set / delete）；会动游戏的命令返回待确认时，
// 循环立刻停下，把待确认做成确认卡交给界面，由用户点。模型点名要不在列表里的工具，回它 not_allowed，不执行。
// 对局本身不靠这个循环：开局后由 hiro 主循环打，agent 只负责发起、看进度、转述。

// ToolSpec 是 hiro tools 里的一条命令。
type ToolSpec struct {
	Name     string    `json:"name"`
	ToolName string    `json:"tool_name"`
	Risk     string    `json:"risk"`
	Help     string    `json:"help"`
	Args     []ToolArg `json:"args"`
	Agent    bool      `json:"agent"`
}

// ToolArg 是命令的一个参数：位置参数写 "kind"，选项写 "--rarity"。
type ToolArg struct {
	Name     string   `json:"name"`
	Help     string   `json:"help"`
	Type     string   `json:"type"`
	Required bool     `json:"required"`
	Choices  []string `json:"choices,omitempty"`
	Agent    *bool    `json:"agent,omitempty"` // false：不给模型（例如 run events --follow 会把对话卡到一局打完）
}

func (a ToolArg) positional() bool { return !strings.HasPrefix(a.Name, "-") }
func (a ToolArg) key() string {
	return strings.ReplaceAll(strings.TrimLeft(a.Name, "-"), "-", "_")
}

// Turn 是交给模型的一轮对话（界面把卡片写成一句话放进 content）。
type Turn struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// Reply 是 agent 这一轮的回答：一段话 + 卡片（形状和前端 src/lib/chat.ts 的 Card 一致）。
type Reply struct {
	Text  string           `json:"text"`
	Cards []map[string]any `json:"cards"`
}

// MaxSteps 是一轮里最多调几次模型（防止模型来回调工具停不下来）。
const MaxSteps = 8

// maxToolResult 是交回模型的工具结果最多多少字节（资料库、回忆列表可能很长）。
const maxToolResult = 12000

// SystemPrompt 是内置 agent 的系统提示词。
const SystemPrompt = `你是「初星助手」，帮玩家玩《学园偶像大师》（学マス）。玩家多半不懂电脑和 AI，用简短的中文白话回答。
始终用简体中文回答，玩家用日文或英文提问也一样；卡名、技能名这些游戏里的专有名词可以在中文后面括注日文原文。

你能用的工具就是 hiro 命令行：
- 查信息（info_*、catalog_*、run_status、run_events）直接调用，回答时说清楚数据是哪次扫描的（scanned_at）；没扫描过就告诉玩家可以让你去扫描。
- 说数量时照抄工具结果里的 counts（total、by_rarity、by_attr、by_timing），不要自己去数 items 列表。
- 玩家问编成、要开局时：先看库存有没有（info_supports / info_memories），再用 plan_formation 出方案并说明理由；要写进游戏时先用 write-formation 的 --dry-run 看差在哪，再正式写。
- 会动游戏的命令（run_start）调用后不会马上执行，而是返回「待确认」：界面会给玩家一张确认卡，玩家点了才开始。你不能替玩家确认。
- 工具报错时，把 message 和 hint 用白话转告玩家。
- 开局前先确认游戏停在首页、没有别的设备上进行中的培育；不知道该用哪个偶像、编成时先问玩家，或者先查信息再建议。

绝不做：花ジュエル、放弃培育（破棄 / リタイア）、读种子或存档里的隐藏信息、让账号密码明文出现（只能用 secret_copy 复制到剪贴板）。`

// ChatProvider 是一个能做工具调用的模型。
type ChatProvider interface {
	Chat(ctx context.Context, messages []chatMessage, tools []toolDef) (chatMessage, error)
}

// Agent 是内置 agent。
type Agent struct {
	Hiro     *Hiro
	Tools    []ToolSpec
	Provider ChatProvider
}

// ---------------------------------------------------------------------------- 工具描述

type toolDef struct {
	Type     string      `json:"type"`
	Function toolDefFunc `json:"function"`
}

type toolDefFunc struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}

func toolDefs(tools []ToolSpec) []toolDef {
	var out []toolDef
	for _, t := range tools {
		if !t.Agent {
			continue
		}
		props := map[string]any{}
		required := []string{}
		for _, a := range t.Args {
			if a.Agent != nil && !*a.Agent {
				continue
			}
			p := map[string]any{"description": a.Help}
			switch a.Type {
			case "int":
				p["type"] = "integer"
			case "bool":
				p["type"] = "boolean"
			default:
				p["type"] = "string"
			}
			if len(a.Choices) > 0 {
				p["enum"] = a.Choices
			}
			props[a.key()] = p
			if a.Required || a.positional() {
				required = append(required, a.key())
			}
		}
		desc := t.Help
		if t.Risk == "act" {
			desc += "（会动游戏：调用后返回待确认，由玩家在界面上确认）"
		}
		out = append(out, toolDef{Type: "function", Function: toolDefFunc{Name: t.ToolName, Description: desc,
			Parameters: map[string]any{"type": "object", "properties": props, "required": required}}})
	}
	return out
}

// toolArgv 把模型给的参数（JSON）换成 hiro 命令行参数。只认命令声明过的参数。
func toolArgv(t ToolSpec, arguments string) ([]string, error) {
	var in map[string]any
	if strings.TrimSpace(arguments) == "" {
		arguments = "{}"
	}
	if err := json.Unmarshal([]byte(arguments), &in); err != nil {
		return nil, fmt.Errorf("参数不是 JSON：%v", err)
	}
	argv := strings.Fields(t.Name)
	var opts []string
	for _, a := range t.Args {
		if a.Agent != nil && !*a.Agent {
			continue // 模型给了也不认
		}
		v, ok := in[a.key()]
		if !ok || v == nil {
			if a.Required || a.positional() {
				return nil, fmt.Errorf("缺参数 %s", a.key())
			}
			continue
		}
		var s string
		switch x := v.(type) {
		case bool:
			if a.Type == "bool" {
				if x {
					opts = append(opts, a.Name)
				}
				continue
			}
			s = strconv.FormatBool(x)
		case float64:
			s = strconv.FormatFloat(x, 'f', -1, 64)
		case string:
			s = x
		default:
			return nil, fmt.Errorf("参数 %s 的类型不对", a.key())
		}
		if a.positional() {
			argv = append(argv, s)
		} else {
			opts = append(opts, a.Name, s)
		}
	}
	return append(argv, opts...), nil
}

// ---------------------------------------------------------------------------- 循环

type chatMessage struct {
	Role       string     `json:"role"`
	Content    string     `json:"content"`
	ToolCalls  []toolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
}

type toolCall struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Function struct {
		Name      string `json:"name"`
		Arguments string `json:"arguments"`
	} `json:"function"`
}

var infoTitles = map[string]string{
	"info supports": "支援卡", "info memories": "回忆", "info idols": "偶像卡", "info affection": "好感度",
	"info game-settings": "游戏设置",
}

// Ask 跑一轮：history 是之前的对话，text 是玩家这次说的话。
func (a *Agent) Ask(ctx context.Context, history []Turn, text string) (Reply, error) {
	defs := toolDefs(a.Tools)
	byName := map[string]ToolSpec{}
	for _, t := range a.Tools {
		if t.Agent {
			byName[t.ToolName] = t
		}
	}
	msgs := []chatMessage{{Role: "system", Content: SystemPrompt}}
	for _, h := range history {
		if h.Role == "user" || h.Role == "assistant" {
			msgs = append(msgs, chatMessage{Role: h.Role, Content: h.Content})
		}
	}
	msgs = append(msgs, chatMessage{Role: "user", Content: text})
	var cards []map[string]any
	for step := 0; step < MaxSteps; step++ {
		m, err := a.Provider.Chat(ctx, msgs, defs)
		if err != nil {
			return Reply{}, err
		}
		if len(m.ToolCalls) == 0 {
			return Reply{Text: m.Content, Cards: cards}, nil
		}
		msgs = append(msgs, chatMessage{Role: "assistant", Content: m.Content, ToolCalls: m.ToolCalls})
		var pendings []*Pending
		for _, c := range m.ToolCalls {
			content, card, pending := a.runTool(ctx, byName, c)
			msgs = append(msgs, chatMessage{Role: "tool", ToolCallID: c.ID, Content: content})
			if card != nil {
				cards = append(cards, card)
			}
			if pending != nil {
				pendings = append(pendings, pending)
			}
		}
		if len(pendings) > 0 {
			var b strings.Builder
			if strings.TrimSpace(m.Content) != "" {
				b.WriteString(strings.TrimSpace(m.Content) + "\n")
			}
			for _, p := range pendings {
				b.WriteString("要开始「" + p.Summary + "」吗？点下面的确认卡。")
			}
			return Reply{Text: b.String(), Cards: cards}, nil
		}
	}
	return Reply{Text: "这件事我绕了好几圈还没办完，换个说法再试一次？", Cards: cards}, nil
}

func (a *Agent) runTool(ctx context.Context, byName map[string]ToolSpec, c toolCall) (string, map[string]any, *Pending) {
	t, ok := byName[c.Function.Name]
	if !ok {
		return `{"ok":false,"error":{"code":"not_allowed","message":"没有这个工具，或者它只能由玩家在界面上操作"}}`, nil, nil
	}
	argv, err := toolArgv(t, c.Function.Arguments)
	if err != nil {
		b, _ := json.Marshal(map[string]any{"ok": false, "error": map[string]string{"code": "bad_args", "message": err.Error()}})
		return string(b), nil, nil
	}
	r := a.Hiro.Call(ctx, argv)
	b, _ := json.Marshal(r)
	content := string(b)
	if len(content) > maxToolResult {
		content = content[:maxToolResult] + "…（太长，截断了；用筛选参数缩小范围）"
	}
	switch {
	case r.Pending != nil:
		return content, map[string]any{"type": "confirm", "pending": r.Pending, "status": "waiting"}, r.Pending
	case !r.OK && r.Error != nil && (r.Error.Code == "hiro_missing" || r.Error.Code == "bad_output" || r.Error.Code == "service_down"):
		return content, map[string]any{"type": "error", "error": r.Error}, nil
	case r.OK && (t.Name == "plan formation" || t.Name == "plan show"):
		var plan map[string]any
		if json.Unmarshal(r.Data, &plan) == nil {
			delete(plan, "report") // 编成卡只要结构；全文在交给模型的结果里
			return content, map[string]any{"type": "formation", "plan": plan}, nil
		}
	case r.OK && infoTitles[t.Name] != "":
		var d struct {
			Items     []map[string]any `json:"items"`
			ScannedAt *string          `json:"scanned_at"`
			Hint      string           `json:"hint"`
		}
		if json.Unmarshal(r.Data, &d) == nil && len(d.Items) > 0 {
			return content, map[string]any{"type": "info", "title": infoTitles[t.Name], "kind": strings.ReplaceAll(strings.TrimPrefix(t.Name, "info "), "-", "_"),
				"items": d.Items, "scannedAt": d.ScannedAt, "hint": d.Hint}, nil
		}
	}
	return content, nil, nil
}

// ---------------------------------------------------------------------------- OpenAI 兼容接口（DeepSeek）

// OpenAICompat 是 OpenAI 兼容的 chat completions 接口（DeepSeek、OpenAI）。
type OpenAICompat struct {
	BaseURL string // 例如 https://api.deepseek.com
	Model   string
	Key     string
	Client  *http.Client
}

// ErrUnauthorized 是 Key 不对。
var ErrUnauthorized = errors.New("API Key 不对或已失效")

func (o OpenAICompat) Chat(ctx context.Context, messages []chatMessage, tools []toolDef) (chatMessage, error) {
	body := map[string]any{"model": o.Model, "messages": messages}
	if len(tools) > 0 {
		body["tools"] = tools
	}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", strings.TrimRight(o.BaseURL, "/")+"/chat/completions", bytes.NewReader(b))
	if err != nil {
		return chatMessage{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+o.Key)
	client := o.Client
	if client == nil {
		client = &http.Client{Timeout: 120 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return chatMessage{}, fmt.Errorf("连不上模型服务：%w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	switch {
	case resp.StatusCode == 401 || resp.StatusCode == 403:
		return chatMessage{}, ErrUnauthorized
	case resp.StatusCode == 402:
		return chatMessage{}, errors.New("模型账户余额不足，去服务商那里充值")
	case resp.StatusCode >= 300:
		msg := string(raw)
		if len(msg) > 300 {
			msg = msg[:300]
		}
		return chatMessage{}, fmt.Errorf("模型服务出错（%d）：%s", resp.StatusCode, msg)
	}
	var out struct {
		Choices []struct {
			Message chatMessage `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(raw, &out); err != nil || len(out.Choices) == 0 {
		return chatMessage{}, errors.New("模型服务的回答看不懂")
	}
	return out.Choices[0].Message, nil
}

// LoadTools 从工具层取命令列表（hiro tools）。
func LoadTools(ctx context.Context, h *Hiro) ([]ToolSpec, error) {
	r := h.Call(ctx, []string{"tools"})
	if !r.OK {
		if r.Error != nil {
			return nil, errors.New(r.Error.Message)
		}
		return nil, errors.New("取不到工具列表")
	}
	var d struct {
		Tools []ToolSpec `json:"tools"`
	}
	if err := json.Unmarshal(r.Data, &d); err != nil {
		return nil, err
	}
	sort.SliceStable(d.Tools, func(i, j int) bool { return d.Tools[i].Name < d.Tools[j].Name })
	return d.Tools, nil
}
