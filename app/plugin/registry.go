package plugin

import (
	"slices"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
)

// Registry 按注册顺序存插件。
type Registry struct {
	mu   sync.RWMutex
	list []*Plugin
}

// Add 注册一个插件（同 ID 的替换掉旧的）。
func (r *Registry) Add(p *Plugin) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i, q := range r.list {
		if q.ID == p.ID {
			r.list[i] = p
			return
		}
	}
	r.list = append(r.list, p)
}

// Remove 去掉一个插件。
func (r *Registry) Remove(id string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i, q := range r.list {
		if q.ID == id {
			r.list = slices.Delete(r.list, i, i+1)
			return
		}
	}
}

// All 是全部插件（注册顺序）。
func (r *Registry) All() []*Plugin {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return slices.Clone(r.list)
}

// ByID 找插件。
func (r *Registry) ByID(id string) *Plugin {
	r.mu.RLock()
	defer r.mu.RUnlock()
	for _, p := range r.list {
		if p.ID == id {
			return p
		}
	}
	return nil
}

// CardView 找画某类卡的插件。
func (r *Registry) CardView(typ string) (*Plugin, CardView) {
	for _, p := range r.All() {
		if v, ok := p.Cards[typ]; ok && v != nil {
			return p, v
		}
	}
	return nil, nil
}

// CommandRef 是一条 "/" 命令和它的插件。
type CommandRef struct {
	Plugin  *Plugin
	Command Command
}

// Commands 是名字以 prefix 开头的 "/" 命令（空前缀为全部）。
func (r *Registry) Commands(prefix string) []CommandRef {
	prefix = strings.TrimSpace(strings.TrimPrefix(prefix, "/"))
	var out []CommandRef
	for _, p := range r.All() {
		for _, c := range p.Commands {
			if strings.HasPrefix(c.Name, prefix) {
				out = append(out, CommandRef{p, c})
			}
		}
	}
	return out
}

// Chips 是全部快捷按钮（注册顺序）。
func (r *Registry) Chips() []Chip {
	var out []Chip
	for _, p := range r.All() {
		out = append(out, p.Chips...)
	}
	return out
}

// ToolOwner 是认领某工具的插件 ID；没人认领为 ""。
func (r *Registry) ToolOwner(tool string) string {
	for _, p := range r.All() {
		for _, t := range p.Tools {
			if t == tool {
				return p.ID
			}
		}
	}
	return ""
}

// Explain 让插件们解读一条待确认；都认不出时 ok=false。
func (r *Registry) Explain(h Host, pd hiroapi.Pending) (Explained, bool) {
	for _, p := range r.All() {
		if p.Explain == nil {
			continue
		}
		if e, ok := p.Explain(h, pd); ok {
			return e, true
		}
	}
	return Explained{}, false
}

// MaxStarters 是新对话页最多几张引导卡。
const MaxStarters = 4

// Starters 是各插件的引导卡（注册顺序，最多 MaxStarters 张）。
func (r *Registry) Starters(h Host) []Starter {
	var out []Starter
	for _, p := range r.All() {
		if p.Starters == nil {
			continue
		}
		for _, s := range p.Starters(h) {
			if len(out) == MaxStarters {
				return out
			}
			out = append(out, s)
		}
	}
	return out
}

// Greeting 是各插件问候小字拼起来（用「 · 」隔开）。
func (r *Registry) Greeting(h Host) string {
	var parts []string
	for _, p := range r.All() {
		if p.Greeting == nil {
			continue
		}
		if g := strings.TrimSpace(p.Greeting(h)); g != "" {
			parts = append(parts, g)
		}
	}
	return strings.Join(parts, " · ")
}
