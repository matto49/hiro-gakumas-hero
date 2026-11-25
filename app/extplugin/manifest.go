// Package extplugin 是外部插件（设计稿 §5.4）：清单 + 子进程（任何语言，stdin/stdout 一行一条 JSON-RPC 2.0），
// 界面由插件用 JSON 描述、初星助手用原生 ui 画；包成 plugin.Plugin 交给宿主，和内置插件走同一套接口。
package extplugin

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// ToolArg 是插件工具的一个参数。
type ToolArg struct {
	Name     string   `json:"name"`
	Help     string   `json:"help"`
	Type     string   `json:"type"`
	Required bool     `json:"required"`
	Choices  []string `json:"choices,omitempty"`
}

// Tool 是插件给 agent 的工具。
type Tool struct {
	Name string    `json:"name"`
	Help string    `json:"help"`
	Args []ToolArg `json:"args"`
}

// Panel 是插件的一个面板页。
type Panel struct {
	Route string `json:"route"`
	Title string `json:"title"`
	Kind  string `json:"kind"` // native / web
	File  string `json:"file,omitempty"`
}

// Command 是插件的 "/" 命令。
type Command struct {
	Name  string `json:"name"`
	Hint  string `json:"hint"`
	Panel string `json:"panel"`
}

// Chip 是插件的快捷按钮（代用户说一句话）。
type Chip struct {
	Label string `json:"label"`
	Say   string `json:"say"`
}

// Manifest 是 plugin.json。
type Manifest struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Version     string   `json:"version"`
	Icon        string   `json:"icon"`
	Command     []string `json:"command"`
	Acts        []string `json:"acts"`
	Contributes struct {
		Tools    []Tool    `json:"tools"`
		Commands []Command `json:"commands"`
		Chips    []Chip    `json:"chips"`
		Cards    []string  `json:"cards"`
		Panels   []Panel   `json:"panels"`
		Settings bool      `json:"settings"`
		Context  bool      `json:"context"`
	} `json:"contributes"`

	// Dir 是插件目录（不在 json 里）。
	Dir string `json:"-"`
}

var (
	idRe   = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)
	toolRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,40}$`)
)

// builtinIDs 是内置插件的 ID，外部插件不能用。
var builtinIDs = map[string]bool{"start": true, "plan": true, "library": true, "cockpit": true, "settings": true}

// LoadManifest 读并校验 dir/plugin.json。
func LoadManifest(dir string) (*Manifest, error) {
	b, err := os.ReadFile(filepath.Join(dir, "plugin.json"))
	if err != nil {
		return nil, fmt.Errorf("读不到 plugin.json：%w", err)
	}
	var m Manifest
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("plugin.json 不是合法的 JSON：%w", err)
	}
	switch {
	case !idRe.MatchString(m.ID):
		return nil, fmt.Errorf("插件 id %q 不合法：只能用小写字母、数字和 -", m.ID)
	case builtinIDs[m.ID]:
		return nil, fmt.Errorf("插件 id %q 和内置插件重名", m.ID)
	case strings.TrimSpace(m.Name) == "":
		return nil, errors.New("插件没有名字（name）")
	case len(m.Command) == 0 || strings.TrimSpace(m.Command[0]) == "":
		return nil, errors.New("插件没写怎么启动（command）")
	}
	for _, a := range m.Acts {
		if forbiddenAct(strings.Fields(a)) {
			return nil, fmt.Errorf("acts 里有不允许插件发起的命令：%q", a)
		}
	}
	for _, t := range m.Contributes.Tools {
		if !toolRe.MatchString(t.Name) {
			return nil, fmt.Errorf("工具名 %q 不合法：只能用英文字母、数字、_ 和 -，最多 40 个字", t.Name)
		}
	}
	for _, p := range m.Contributes.Panels {
		if p.Kind == "web" && safePath(dir, "", p.File) == "" {
			return nil, fmt.Errorf("网页面板 %q 的文件不在插件目录里", p.Route)
		}
	}
	abs, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	m.Dir = abs
	return &m, nil
}
