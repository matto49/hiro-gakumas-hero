package shell

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync/atomic"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// ThreadStore 是对话存储（main 里的 Threads：HIRO_HOME/threads/<id>.json，原样存取）。
type ThreadStore interface {
	List() []json.RawMessage
	Save(id string, thread json.RawMessage) error
	Delete(id string) error
}

// Message 是一条消息；形状和旧版 src/lib/chat.ts 一致（role 是 user / agent，at 是毫秒）。
type Message struct {
	ID    string        `json:"id"`
	Role  string        `json:"role"`
	Text  string        `json:"text,omitempty"`
	Cards []plugin.Card `json:"cards"`
	At    int64         `json:"at"`
	// Tools 是这一轮 agent 调过的工具（「info_idols · 找到 1 张」），新版才有。
	Tools []string `json:"tools,omitempty"`
}

// Thread 是一个对话。
type Thread struct {
	ID        string     `json:"id"`
	Title     string     `json:"title"`
	Messages  []*Message `json:"messages"`
	UpdatedAt int64      `json:"updatedAt"`
}

var seq atomic.Int64

func uid(prefix string, now time.Time) string {
	return fmt.Sprintf("%s-%s-%d", prefix, strings.ToLower(fmt.Sprintf("%x", now.UnixMilli())), seq.Add(1))
}

// titleFrom 用第一句话当标题（最多 20 字）。
func titleFrom(text string) string {
	r := []rune(strings.TrimSpace(strings.ReplaceAll(text, "\n", " ")))
	if len(r) > 20 {
		return string(r[:20]) + "…"
	}
	if len(r) == 0 {
		return "新对话"
	}
	return string(r)
}

func loadThreads(store ThreadStore) []*Thread {
	var out []*Thread
	for _, raw := range store.List() {
		var t Thread
		if json.Unmarshal(raw, &t) != nil || t.ID == "" {
			continue
		}
		msgs := t.Messages[:0]
		for _, m := range t.Messages {
			if m == nil { // 坏掉的存档（null 消息）：跳过这条，别让程序起不来
				continue
			}
			if m.Cards == nil {
				m.Cards = []plugin.Card{}
			}
			for _, card := range m.Cards {
				delete(card, "sending") // 「再试一次」发起中只在这次运行里有效：程序关了回调不会再来，留着按钮会一直灰（#110 review）
			}
			msgs = append(msgs, m)
		}
		t.Messages = msgs
		out = append(out, &t)
	}
	return out
}
