// Package vision 是识屏层（同 Python 版 hiro.vision）：截图 → 页面判定与字段读数。
//
// 照 Python 逐个函数搬，判据、阈值、顺序都不改；每个函数在黄金截图上和 Python 版对照（*_test.go，见 internal/golden）。
// 这个文件是 Python / numpy 语义的小工具：Python 的 re 对 str 是 Unicode 的 \d、\s，int() 认全角数字，np.median 偶数个取中间两个的平均。
package vision

import (
	"math"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Python re 的 \d、\s（str 模式，Unicode）：RE2 的 \d、\s 只认 ASCII。
const (
	reD = `\p{Nd}`
	reS = `[\t\n\v\f\r\x1c-\x1f \x{85}\p{Z}]`
)

// pyRe 编译正则，先把 Python 写法的 \d、\s 换成 Unicode 版（字符类里外都换）。
func pyRe(expr string) *regexp.Regexp {
	return regexp.MustCompile(pyExpr(expr))
}

func pyExpr(expr string) string {
	var b strings.Builder
	inClass := false
	for i := 0; i < len(expr); i++ {
		c := expr[i]
		if c == '\\' && i+1 < len(expr) {
			n := expr[i+1]
			switch {
			case n == 'd' && inClass:
				b.WriteString(`\p{Nd}`)
			case n == 'd':
				b.WriteString(reD)
			case n == 's' && inClass:
				b.WriteString(`\t\n\v\f\r\x1c-\x1f \x{85}\p{Z}`)
			case n == 's':
				b.WriteString(reS)
			default:
				b.WriteByte(c)
				b.WriteByte(n)
			}
			i++
			continue
		}
		switch c {
		case '[':
			inClass = true
		case ']':
			inClass = false
		}
		b.WriteByte(c)
	}
	return b.String()
}

// pySpace 同 Python 的 str.isspace。
func pySpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) || unicode.Is(unicode.Z, r)
}

// pyStrip 同 Python 的 str.strip()。
func pyStrip(s string) string { return strings.TrimFunc(s, pySpace) }

// pyAtoi 同 Python 的 int(str)：认 Unicode 数字（全角等），两边可以有空白；不是数字返回 ok=false。
func pyAtoi(s string) (int, bool) {
	s = pyStrip(s)
	if s == "" {
		return 0, false
	}
	neg := false
	switch s[0] {
	case '-':
		neg, s = true, s[1:]
	case '+':
		s = s[1:]
	}
	n := 0
	any := false
	for _, r := range strings.ReplaceAll(s, "_", "") {
		d := digitValue(r)
		if d < 0 {
			return 0, false
		}
		n, any = n*10+d, true
	}
	if !any {
		return 0, false
	}
	if neg {
		n = -n
	}
	return n, true
}

// mustAtoi 正则已保证是数字时用。
func mustAtoi(s string) int {
	n, _ := pyAtoi(s)
	return n
}

// digitValue Unicode 十进制数字的值：ASCII、全角（OCR 里只见过这两种）；别的 Nd 按「往前数到这一组的 0」算
// （Nd 都是 0–9 连续排的组，相邻两组紧挨着的只有数学字母数字区，OCR 不会出）。
func digitValue(r rune) int {
	switch {
	case r >= '0' && r <= '9':
		return int(r - '0')
	case r >= '０' && r <= '９':
		return int(r - '０')
	case !unicode.Is(unicode.Nd, r):
		return -1
	}
	d := 0
	for d < 9 && unicode.Is(unicode.Nd, r-rune(d)-1) {
		d++
	}
	return d
}

// median 同 np.median：排序后取中间，偶数个取中间两个的平均。空的返回 NaN。
func median(v []byte) float64 {
	if len(v) == 0 {
		return math.NaN()
	}
	s := slices.Clone(v)
	slices.Sort(s)
	n := len(s)
	if n%2 == 1 {
		return float64(s[n/2])
	}
	return (float64(s[n/2-1]) + float64(s[n/2])) / 2
}

// chanOf 取三通道图（BGR 或 HSV）的一个通道的全部像素。
func chanOf(b *img.BGR, c int) []byte {
	out := make([]byte, b.W*b.H)
	for i := range out {
		out[i] = b.Pix[i*3+c]
	}
	return out
}

// fracWhere 三通道图里满足 f(像素) 的比例（numpy 布尔数组的 .mean()）。空图返回 NaN。
func fracWhere(b *img.BGR, f func(p []byte) bool) float64 {
	n := b.W * b.H
	if n == 0 {
		return math.NaN()
	}
	k := 0
	for i := range n {
		if f(b.Pix[i*3 : i*3+3]) {
			k++
		}
	}
	return float64(k) / float64(n)
}

// countWhere 满足 f 的像素个数（numpy 布尔数组的 .sum()）。
func countWhere(b *img.BGR, f func(p []byte) bool) int {
	k := 0
	for i := range b.W * b.H {
		if f(b.Pix[i*3 : i*3+3]) {
			k++
		}
	}
	return k
}

// grayFrac 单通道图里满足 f 的比例。
func grayFrac(g *img.Gray, f func(v byte) bool) float64 {
	if len(g.Pix) == 0 {
		return math.NaN()
	}
	k := 0
	for _, v := range g.Pix {
		if f(v) {
			k++
		}
	}
	return float64(k) / float64(len(g.Pix))
}

// size numpy 的 .size（三通道图是 W*H*3）。
func size(b *img.BGR) int {
	if b == nil {
		return 0
	}
	return b.W * b.H * 3
}

// npSum 同 numpy 对一维 float64 数组求和的成对累加（pairwise_sum：不到 8 个顺序加；≤128 个 8 路累加；更多就对半分）。
func npSum(v []float64) float64 {
	n := len(v)
	switch {
	case n < 8:
		s := 0.0
		for _, x := range v {
			s += x
		}
		return s
	case n <= 128:
		var r [8]float64
		copy(r[:], v[:8])
		i := 8
		for ; i < n-n%8; i += 8 {
			for j := range 8 {
				r[j] += v[i+j]
			}
		}
		s := ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
		for ; i < n; i++ {
			s += v[i]
		}
		return s
	}
	n2 := n / 2
	n2 -= n2 % 8
	return npSum(v[:n2]) + npSum(v[n2:])
}

// npMean 同 np.mean（一维 float64）。空的返回 NaN。
func npMean(v []float64) float64 {
	if len(v) == 0 {
		return math.NaN()
	}
	return npSum(v) / float64(len(v))
}

// sortStable 稳定排序（Python 的 sorted / list.sort 都是稳定的）。
func sortStable[T any](v []T, less func(a, b T) bool) {
	sort.SliceStable(v, func(i, j int) bool { return less(v[i], v[j]) })
}

func itoa(n int) string { return strconv.Itoa(n) }

// pyRound 同 Python 的 round(x, n)：按 x 的精确二进制值四舍六入五成双到 n 位小数（strconv 的定点格式也是精确舍入）。
func pyRound(x float64, n int) float64 {
	if math.IsNaN(x) || math.IsInf(x, 0) {
		return x
	}
	v, _ := strconv.ParseFloat(strconv.FormatFloat(x, 'f', n, 64), 64)
	return v
}
