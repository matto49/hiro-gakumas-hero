// Package secret 是 `hiro secret …`：游戏账号密码。同 Python 版 hiro.cli.secrets。
//
//   - 值只由人填写：`hiro secret set <名>` 只从标准输入读（设置页把输入框的值写进标准输入），不收命令行参数。
//   - 存在系统的凭据库里：Mac 钥匙串、Windows 凭据管理器。条目格式同 Python 版（服务 hiro-console、账户 = 名字；
//     Windows 目标名 hiro-console:<名字>、值 UTF-16），两边存的能互相读。本机只另存名字清单 secrets.json。
//   - 没有任何命令会打印值：list 只给名字；copy 把值带敏感标记写进剪贴板，45 秒后清空（期间用户又复制了别的就不清）。
//     报错信息里也不带值。
//   - HIRO_SECRET_BACKEND=memory：测试用的进程内后端。
package secret

import (
	"encoding/binary"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"unicode/utf16"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// Service 是凭据库里的服务名。
const Service = "hiro-console"

// ClearAfter 是剪贴板多少秒后清空。
const ClearAfter = 45

// ClearCommand 是 hiro 的隐藏子命令：Windows 上延时清剪贴板的后台进程（hiro __clear-clipboard <序号> <秒>）。
const ClearCommand = "__clear-clipboard"

var namePattern = regexp.MustCompile(`^[A-Za-z0-9_.@-]{1,64}$`)

// Backend 是凭据库。Get 没有这一项时返回 ""、nil。
type Backend interface {
	Set(name, value string) error
	Get(name string) (string, error)
	Delete(name string) error
}

// ---------------------------------------------------------------- 测试用的进程内后端

type memoryBackend struct{}

var (
	memMu sync.Mutex
	mem   = map[string]string{}
)

func resetMemory() { memMu.Lock(); mem = map[string]string{}; memMu.Unlock() }

func (memoryBackend) Set(n, v string) error {
	memMu.Lock()
	defer memMu.Unlock()
	mem[n] = v
	return nil
}

func (memoryBackend) Get(n string) (string, error) {
	memMu.Lock()
	defer memMu.Unlock()
	return mem[n], nil
}

func (memoryBackend) Delete(n string) error {
	memMu.Lock()
	defer memMu.Unlock()
	delete(mem, n)
	return nil
}

func backend() Backend {
	if os.Getenv("HIRO_SECRET_BACKEND") == "memory" {
		return memoryBackend{}
	}
	return systemBackend()
}

// ---------------------------------------------------------------- 名字清单

func indexPath() (string, error) {
	h, err := home.Ensure()
	if err != nil {
		return "", err
	}
	return filepath.Join(h, "secrets.json"), nil
}

// Names 是保存了的名字（排好序）。
func Names() []string {
	p, err := indexPath()
	if err != nil {
		return nil
	}
	data, err := os.ReadFile(p)
	if err != nil {
		return nil
	}
	var names []string
	if json.Unmarshal(data, &names) != nil {
		return nil
	}
	return names
}

func saveNames(names []string) error {
	set := map[string]bool{}
	out := []string{}
	for _, n := range names {
		if !set[n] {
			set[n] = true
			out = append(out, n)
		}
	}
	sort.Strings(out)
	p, err := indexPath()
	if err != nil {
		return err
	}
	data, err := jsonx.Marshal(out)
	if err != nil {
		return err
	}
	return os.WriteFile(p, data, 0o600)
}

func checkName(name string) (string, error) {
	if !namePattern.MatchString(name) {
		return "", clierr.New("bad_args", "名字只能用字母、数字和 _ . @ -（最长 64）", "例如 game-account、game-password")
	}
	return name, nil
}

func has(name string) bool {
	for _, n := range Names() {
		if n == name {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- UTF-16（Windows 凭据管理器、和 Python 版一致）

func toUTF16(s string) []byte {
	u := utf16.Encode([]rune(s))
	b := make([]byte, 0, len(u)*2)
	for _, c := range u {
		b = binary.LittleEndian.AppendUint16(b, c)
	}
	return b
}

func fromUTF16(b []byte) string {
	u := make([]uint16, 0, len(b)/2)
	for i := 0; i+1 < len(b); i += 2 {
		u = append(u, binary.LittleEndian.Uint16(b[i:]))
	}
	return string(utf16.Decode(u))
}

// ---------------------------------------------------------------- 命令

// writeClipboard 带敏感标记写剪贴板，clearAfter 秒后清空（测试里换成假的）。
var writeClipboard = systemClipboard

// Register 登记 `hiro secret …` 命令。
func Register(r *cli.Registry) {
	r.MustRegister(
		&cli.Command{
			Name: "secret set", Risk: cli.Read, NoAgent: true,
			Help: "保存一个账号或密码（值只从标准输入读，只由用户在设置页填写）。",
			Args: []cli.Arg{{Name: "name", Help: "名字，例如 game-account、game-password"}},
			Handler: func(ctx *cli.Ctx, a cli.Args) (any, error) {
				name, err := checkName(a.Str("name"))
				if err != nil {
					return nil, err
				}
				var raw []byte
				if ctx.Stdin != nil {
					raw, err = io.ReadAll(io.LimitReader(ctx.Stdin, 64<<10))
					if err != nil {
						return nil, clierr.New("empty_secret", "没有读到要保存的值", "在设置页的输入框里填好再保存")
					}
				}
				value := strings.TrimRight(string(raw), "\r\n")
				if value == "" {
					return nil, clierr.New("empty_secret", "没有读到要保存的值", "在设置页的输入框里填好再保存")
				}
				if err := backend().Set(name, value); err != nil {
					return nil, err
				}
				if err := saveNames(append(Names(), name)); err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("saved", name), nil
			},
		},
		&cli.Command{
			Name: "secret list", Risk: cli.Read,
			Help: "列出保存了哪些账号密码（只有名字，不显示值）。",
			Handler: func(*cli.Ctx, cli.Args) (any, error) {
				list := []any{}
				for _, n := range Names() {
					list = append(list, jsonx.NewObject().Set("name", n).Set("saved", true))
				}
				return jsonx.NewObject().Set("secrets", list), nil
			},
		},
		&cli.Command{
			Name: "secret copy", Risk: cli.Read,
			Help: "把一个账号或密码复制到剪贴板（带敏感标记，45 秒后自动清空），用来粘贴进游戏登录框。不会显示值。",
			Args: []cli.Arg{{Name: "name", Help: "名字，例如 game-account"}},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				name, err := checkName(a.Str("name"))
				if err != nil {
					return nil, err
				}
				value := ""
				if has(name) {
					if value, err = backend().Get(name); err != nil {
						return nil, err
					}
				}
				if value == "" {
					return nil, clierr.New("secret_missing", "没有保存 "+name, "请用户在设置 → 账号密码里填写")
				}
				if err := writeClipboard(value, ClearAfter); err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("copied", name).Set("clears_in", ClearAfter), nil
			},
		},
		&cli.Command{
			Name: "secret delete", Risk: cli.Read, NoAgent: true,
			Help: "删除一个保存的账号或密码。",
			Args: []cli.Arg{{Name: "name", Help: "名字"}},
			Handler: func(_ *cli.Ctx, a cli.Args) (any, error) {
				name, err := checkName(a.Str("name"))
				if err != nil {
					return nil, err
				}
				if err := backend().Delete(name); err != nil {
					return nil, err
				}
				var keep []string
				for _, n := range Names() {
					if n != name {
						keep = append(keep, n)
					}
				}
				if err := saveNames(keep); err != nil {
					return nil, err
				}
				return jsonx.NewObject().Set("deleted", name), nil
			},
		},
	)
}
