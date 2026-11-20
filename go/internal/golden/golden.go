// Package golden 是识别层的黄金数据对照（只在测试里用）：tools/golden/export_vision.py 导出的截图 + Python 版结果，
// 和 tools/golden/probe.py 逐函数跑出来的 Python 结果。Go 测试在同一批像素上跑 Go 版，逐张比。
//
// 数据在 HIRO_GOLDEN（缺省 ~/.cache/hiro/golden/vision），不进仓库；目录不在就跳过。
// 逐函数对照要 HIRO_PARITY_PYTHON（装了 pyobjc Vision 的 Python，如 ~/github/digital-hiro/.venv/bin/python）；
// 结果按「函数 + 参数 + manifest 修改时间」缓存在 <golden>/probe/，同样的参数只跑一次。
package golden

import (
	"bufio"
	"bytes"
	"crypto/sha1" //nolint:gosec // 只做缓存文件名
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

// Dir 黄金数据目录。
func Dir() string {
	if v := os.Getenv("HIRO_GOLDEN"); v != "" {
		return v
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".cache", "hiro", "golden", "vision")
}

// Case 是一张截图。
type Case struct {
	ID   string `json:"id"`
	Src  string `json:"src"`
	Page string `json:"page"`
	Err  bool   `json:"error"`
	dir  string
}

// Path 无损像素 png 的路径。
func (c Case) Path() string { return filepath.Join(c.dir, c.ID+".png") }

// Image 读截图（cv2.imread 同款）。
func (c Case) Image(t testing.TB) *img.BGR {
	t.Helper()
	b, err := cv.Read(c.Path())
	if err != nil {
		t.Fatalf("%s：%v", c.ID, err)
	}
	return b
}

// Name 测试里显示的名字：来源文件名（截短）+ 编号。
func (c Case) Name() string {
	base := filepath.Base(c.Src)
	if p := filepath.Base(filepath.Dir(c.Src)); p != "" {
		base = p + "/" + base
	}
	return base + "#" + c.ID
}

// Record 是 export_vision.py 给每张截图写的结果。
type Record struct {
	Src      string `json:"src"`
	W        int    `json:"w"`
	H        int    `json:"h"`
	OCR      string `json:"ocr"`
	Classify *struct {
		Name   string          `json:"name"`
		Source string          `json:"source"`
		Area   string          `json:"area"`
		Fields json.RawMessage `json:"fields"`
	} `json:"classify"`
	Extract *struct {
		Scene  string          `json:"scene"`
		Fields json.RawMessage `json:"fields"`
	} `json:"extract"`
	Error string `json:"error"`
}

// Record 读这张截图的导出结果。
func (c Case) Record(t testing.TB) Record {
	t.Helper()
	var r Record
	b, err := os.ReadFile(filepath.Join(c.dir, c.ID+".json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

var setupOnce sync.Once

// setup 让 Go 这边和导出时用同一套素材：没设 HIRO_KAA_GAME_DATA 时指向 digital-hiro 仓库的公开数据（导出时就用它）。
func setup() {
	setupOnce.Do(func() {
		if os.Getenv("HIRO_KAA_GAME_DATA") == "" {
			h, _ := os.UserHomeDir()
			if d := filepath.Join(h, "github", "digital-hiro", "resources", "game_data"); dirExists(d) {
				_ = os.Setenv("HIRO_KAA_GAME_DATA", d)
			}
		}
		_ = os.Setenv("HIRO_OCR", "vision")
		assets.Reset()
	})
}

func dirExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// Cases 黄金数据里有像素的截图（manifest 顺序）；目录不在就跳过测试。HIRO_GOLDEN_LIMIT 限张数（调试用）。
func Cases(t testing.TB) []Case {
	t.Helper()
	setup()
	dir := Dir()
	f, err := os.Open(filepath.Join(dir, "manifest.jsonl"))
	if err != nil {
		t.Skipf("没有黄金数据（%s）：先跑 tools/golden/export_vision.py", dir)
	}
	defer func() { _ = f.Close() }()
	var out []Case
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		var c Case
		if json.Unmarshal(sc.Bytes(), &c) != nil || c.ID == "" {
			continue
		}
		c.dir = dir
		if _, err := os.Stat(c.Path()); err == nil {
			out = append(out, c)
		}
	}
	if n := envInt("HIRO_GOLDEN_LIMIT"); n > 0 && n < len(out) {
		out = out[:n]
	}
	return out
}

func envInt(name string) int {
	var n int
	_, _ = fmt.Sscan(os.Getenv(name), &n)
	return n
}

// Result 是 Python 版某个函数在一张截图上的结果。
type Result struct {
	ID    string          `json:"id"`
	Out   json.RawMessage `json:"out"`
	Error string          `json:"error"`
}

// Probe 在每张截图上跑 Python 版的 call（"模块:函数"），返回 编号 → 结果。args / kwargs 是截图后面的参数。
func Probe(t testing.TB, call string, args []any, kwargs map[string]any, ids []string) map[string]Result {
	t.Helper()
	setup()
	py := os.Getenv("HIRO_PARITY_PYTHON")
	if py == "" {
		t.Skip("逐函数对照要 HIRO_PARITY_PYTHON（装了 pyobjc Vision 的 Python）")
	}
	if args == nil {
		args = []any{}
	}
	if kwargs == nil {
		kwargs = map[string]any{}
	}
	aj, _ := json.Marshal(args)
	kj, _ := json.Marshal(kwargs)
	dir := Dir()
	mf, err := os.Stat(filepath.Join(dir, "manifest.jsonl"))
	if err != nil {
		t.Skipf("没有黄金数据（%s）", dir)
	}
	idList := strings.Join(ids, ",")
	sum := sha1.Sum([]byte(call + "\x00" + string(aj) + "\x00" + string(kj) + "\x00" + idList + "\x00" + mf.ModTime().String())) //nolint:gosec // 缓存文件名
	cache := filepath.Join(dir, "probe", strings.ReplaceAll(call, ":", ".")+"-"+hex.EncodeToString(sum[:])[:10]+".jsonl")
	if _, err := os.Stat(cache); err != nil {
		repo, err := pybridge.Repo()
		if err != nil {
			t.Skip(err)
		}
		if err := os.MkdirAll(filepath.Dir(cache), 0o750); err != nil {
			t.Fatal(err)
		}
		tmp := cache + ".tmp"
		cmd := exec.Command(py, filepath.Join(repo, "tools", "golden", "probe.py"), "--golden", dir, "--call", call, //nolint:gosec // 测试：本机配置的 Python
			"--args", string(aj), "--kwargs", string(kj), "--ids", idList, "--out", tmp)
		cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(repo, "src"), "HIRO_OCR=vision")
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		if err := cmd.Run(); err != nil {
			t.Fatalf("probe.py %s：%v\n%s", call, err, tail(stderr.String(), 2000))
		}
		if err := os.Rename(tmp, cache); err != nil {
			t.Fatal(err)
		}
	}
	b, err := os.ReadFile(cache)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]Result{}
	for _, line := range bytes.Split(b, []byte("\n")) {
		if len(line) == 0 {
			continue
		}
		var r Result
		if err := json.Unmarshal(line, &r); err != nil {
			t.Fatalf("%s：%v", cache, err)
		}
		out[r.ID] = r
	}
	return out
}

func tail(s string, n int) string {
	if len(s) > n {
		return s[len(s)-n:]
	}
	return s
}

// Diff 比较 Go 结果和 Python 结果（JSON 语义比较；浮点差 tol 以内算一样）。一样返回空串，否则返回第一处不同。
// got 先按 encoding/json 编码（字段名用 json tag 对齐 Python 的键）；ignore 里的键两边都去掉。
func Diff(got any, want json.RawMessage, tol float64, ignore ...string) string {
	gb, err := json.Marshal(got)
	if err != nil {
		return "Go 结果编不成 JSON：" + err.Error()
	}
	var g, w any
	if err := json.Unmarshal(gb, &g); err != nil {
		return err.Error()
	}
	if len(want) == 0 {
		want = json.RawMessage("null")
	}
	if err := json.Unmarshal(want, &w); err != nil {
		return err.Error()
	}
	if len(ignore) > 0 {
		drop := map[string]bool{}
		for _, k := range ignore {
			drop[k] = true
		}
		g, w = strip(g, drop), strip(w, drop)
	}
	return diff("$", g, w, tol)
}

func strip(v any, drop map[string]bool) any {
	switch x := v.(type) {
	case map[string]any:
		for k := range x {
			if drop[k] {
				delete(x, k)
			} else {
				x[k] = strip(x[k], drop)
			}
		}
	case []any:
		for i := range x {
			x[i] = strip(x[i], drop)
		}
	}
	return v
}

func diff(path string, g, w any, tol float64) string {
	switch wv := w.(type) {
	case map[string]any:
		gv, ok := g.(map[string]any)
		if !ok {
			return fmt.Sprintf("%s：go %s / python %s", path, short(g), short(w))
		}
		keys := map[string]bool{}
		for k := range wv {
			keys[k] = true
		}
		for k := range gv {
			keys[k] = true
		}
		ks := make([]string, 0, len(keys))
		for k := range keys {
			ks = append(ks, k)
		}
		sort.Strings(ks)
		for _, k := range ks {
			gx, gok := gv[k]
			wx, wok := wv[k]
			if !gok || !wok {
				return fmt.Sprintf("%s.%s：go %s / python %s", path, k, presence(gx, gok), presence(wx, wok))
			}
			if d := diff(path+"."+k, gx, wx, tol); d != "" {
				return d
			}
		}
		return ""
	case []any:
		gv, ok := g.([]any)
		if !ok || len(gv) != len(wv) {
			return fmt.Sprintf("%s：go %s / python %s", path, short(g), short(w))
		}
		for i := range wv {
			if d := diff(fmt.Sprintf("%s[%d]", path, i), gv[i], wv[i], tol); d != "" {
				return d
			}
		}
		return ""
	case float64:
		gv, ok := g.(float64)
		if !ok || math.Abs(gv-wv) > tol {
			return fmt.Sprintf("%s：go %s / python %s", path, short(g), short(w))
		}
		return ""
	default:
		if g != w {
			return fmt.Sprintf("%s：go %s / python %s", path, short(g), short(w))
		}
		return ""
	}
}

func presence(v any, ok bool) string {
	if !ok {
		return "（没有）"
	}
	return short(v)
}

func short(v any) string {
	b, _ := json.Marshal(v)
	s := string(b)
	if len(s) > 160 {
		s = s[:160] + "…"
	}
	return s
}

// Spec 是一次逐函数对照。
type Spec struct {
	Call   string         // Python 的 "模块:函数"（tools/golden 下的 probes 模块也能用）
	Args   []any          // 截图后面的参数
	Kwargs map[string]any //
	Tol    float64        // 浮点容差
	Allow  int            // 允许几张不一样
	Sample int            // 只取均匀分布的这么多张（0 = 全部；慢的函数用）
	Ignore []string       // 两边都不比的键（任意层级，如耗时 "ms"）
}

// Sample 均匀取 n 张（n <= 0 或不少于总数时原样返回）。
func Sample(cases []Case, n int) []Case {
	if n <= 0 || n >= len(cases) {
		return cases
	}
	out := make([]Case, 0, n)
	for i := range n {
		out = append(out, cases[i*len(cases)/n])
	}
	return out
}

// Compare 逐张跑 fn，和 Python 的 Call 比；不一样的张数超过 Allow 时测试失败，列出前 20 处。
// fn 返回 (结果, error)：Python 出错时 Go 也要出错（不比消息）。
func Compare(t *testing.T, s Spec, fn func(*img.BGR) (any, error)) {
	t.Helper()
	call, tol, allow := s.Call, s.Tol, s.Allow
	cases := Cases(t)
	var ids []string
	if s.Sample > 0 && s.Sample < len(cases) {
		cases = Sample(cases, s.Sample)
		for _, c := range cases {
			ids = append(ids, c.ID)
		}
	}
	want := Probe(t, call, s.Args, s.Kwargs, ids)
	var bad []string
	n := 0
	for _, c := range cases {
		w, ok := want[c.ID]
		if !ok {
			continue
		}
		n++
		got, err := fn(c.Image(t))
		switch {
		case w.Error != "" && err == nil:
			bad = append(bad, fmt.Sprintf("%s：python 出错（%s），go 没有：%s", c.Name(), w.Error, short(got)))
		case w.Error == "" && err != nil:
			bad = append(bad, fmt.Sprintf("%s：go 出错 %v", c.Name(), err))
		case w.Error == "":
			if d := Diff(got, w.Out, tol, s.Ignore...); d != "" {
				bad = append(bad, c.Name()+" "+d)
			}
		}
	}
	if n == 0 {
		t.Skip("没有可比的截图")
	}
	for i, b := range bad {
		if i == 20 {
			t.Logf("……还有 %d 处", len(bad)-20)
			break
		}
		t.Log(b)
	}
	if len(bad) > allow {
		t.Errorf("%s：%d / %d 张不一样（允许 %d）", call, len(bad), n, allow)
	} else {
		t.Logf("%s：%d 张，%d 张不一样（允许 %d）", call, n, len(bad), allow)
	}
}
