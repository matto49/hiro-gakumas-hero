// Package pending 是待确认：会动游戏的命令第一次执行不真的做，返回一个待确认编号；用户点了才 `hiro confirm <编号>`。
//
//   - 编号一次性：确认时先把记录原子地拿走再执行；10 分钟过期。
//   - 记录在 HIRO_HOME/pending/<编号>.json，格式同 Python 版 hiro.cli.pending（两边能确认对方发起的待确认）。
package pending

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// TTL 是待确认的有效期。
const TTL = 10 * time.Minute

var (
	idPattern = regexp.MustCompile(`^[0-9a-f]{12}$`)
	now       = time.Now // 测试里换
)

// Record 是一条待确认。Args 是命令的参数（键是参数名去掉 -- 后把 - 换成 _，没给的是 null）。
type Record struct {
	ID        string
	Command   string
	Args      *jsonx.Object
	Summary   string
	CreatedAt float64
	ExpiresAt float64
}

// Brief 是给界面和 agent 看的那几项。
type Brief struct {
	ID        string  `json:"id"`
	Command   string  `json:"command"`
	Summary   string  `json:"summary"`
	ExpiresAt float64 `json:"expires_at"`
}

// Brief 返回给界面看的那几项。
func (r *Record) Brief() Brief {
	return Brief{ID: r.ID, Command: r.Command, Summary: r.Summary, ExpiresAt: r.ExpiresAt}
}

func unknown(id string) error {
	return clierr.New("pending_unknown", "没有这条待确认："+id, "待确认只能用一次；重新发起这条命令")
}

func dir() (string, error) { return home.Sub("pending") }

func epoch(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// Create 记一条待确认。
func Create(command string, args *jsonx.Object, summary string) (*Record, error) {
	d, err := dir()
	if err != nil {
		return nil, err
	}
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return nil, err
	}
	t := epoch(now())
	rec := &Record{ID: hex.EncodeToString(b), Command: command, Args: args, Summary: summary, CreatedAt: t, ExpiresAt: t + TTL.Seconds()}
	o := jsonx.NewObject().Set("id", rec.ID).Set("command", command).Set("args", args).Set("summary", summary).
		Set("created_at", rec.CreatedAt).Set("expires_at", rec.ExpiresAt)
	data, err := jsonx.Marshal(o)
	if err != nil {
		return nil, err
	}
	tmp := filepath.Join(d, rec.ID+".tmp")
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return nil, err
	}
	return rec, os.Rename(tmp, filepath.Join(d, rec.ID+".json"))
}

func parse(data []byte) (*Record, error) {
	o, err := jsonx.DecodeObject(data)
	if err != nil {
		return nil, err
	}
	num := func(k string) float64 {
		v, _ := o.Get(k)
		if n, ok := v.(interface{ Float64() (float64, error) }); ok {
			f, _ := n.Float64()
			return f
		}
		return 0
	}
	args := jsonx.Obj(o, "args")
	if args == nil {
		args = jsonx.NewObject()
	}
	return &Record{
		ID: jsonx.Str(o, "id"), Command: jsonx.Str(o, "command"), Args: args, Summary: jsonx.Str(o, "summary"),
		CreatedAt: num("created_at"), ExpiresAt: num("expires_at"),
	}, nil
}

// Take 拿走一条待确认（之后同一编号再确认就是 pending_unknown）。过期的也拿走，但报 pending_expired。
func Take(id string) (*Record, error) {
	if !idPattern.MatchString(id) {
		return nil, unknown(id)
	}
	d, err := dir()
	if err != nil {
		return nil, err
	}
	src := filepath.Join(d, id+".json")
	claimed := filepath.Join(d, fmt.Sprintf("%s.%d.claimed", id, os.Getpid()))
	if err := os.Rename(src, claimed); err != nil { // 原子：两个进程同时确认，只有一个拿得到
		if errors.Is(err, fs.ErrNotExist) {
			return nil, unknown(id)
		}
		return nil, err
	}
	data, err := os.ReadFile(claimed)
	_ = os.Remove(claimed)
	if err != nil {
		return nil, err
	}
	rec, err := parse(data)
	if err != nil {
		return nil, clierr.New("pending_unknown", "这条待确认的记录坏了："+id, "重新发起这条命令")
	}
	if epoch(now()) > rec.ExpiresAt {
		return nil, clierr.New("pending_expired", "这条待确认已经过期（超过 10 分钟）", "重新发起这条命令")
	}
	return rec, nil
}

// Cancel 取消一条待确认；没有这条返回 false。
func Cancel(id string) bool {
	if !idPattern.MatchString(id) {
		return false
	}
	d, err := dir()
	if err != nil {
		return false
	}
	return os.Remove(filepath.Join(d, id+".json")) == nil
}

// Listing 列出还没过期的待确认（按编号排）；顺手删掉过期的。
func Listing() ([]Brief, error) {
	d, err := dir()
	if err != nil {
		return nil, err
	}
	paths, err := filepath.Glob(filepath.Join(d, "*.json"))
	if err != nil {
		return nil, err
	}
	sort.Strings(paths)
	t := epoch(now())
	out := []Brief{}
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		rec, err := parse(data)
		if err != nil {
			continue
		}
		if t > rec.ExpiresAt {
			_ = os.Remove(p)
			continue
		}
		out = append(out, rec.Brief())
	}
	return out, nil
}
