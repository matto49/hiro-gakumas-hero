package ui

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/golden"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

func names(t *testing.T) []string {
	t.Helper()
	c, _ := catalog()
	out := make([]string, 0, len(c.Templates))
	for n := range c.Templates {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}

func TestCatalog(t *testing.T) {
	ns := names(t)
	if len(ns) < 100 {
		t.Fatalf("只有 %d 个模板", len(ns))
	}
	for _, n := range ns {
		if _, err := os.Stat(Tpl(n).Path()); err != nil {
			t.Errorf("%s：%v", n, err)
		}
	}
	if b := Box("InProduce.BoxNoSkillCard"); b.X1 != 180 || b.Y2 != 1026 {
		t.Fatalf("box %v", b)
	}
	_ = filepath.Join
}

func round4(v float64) float64 { return math.RoundToEven(v*1e4) / 1e4 }

// 每个模板在黄金截图上的全部命中，和 Python 的 find_all 一样（框一样，分数差 1e-3 以内）。
func TestTemplatesMatchPython(t *testing.T) {
	ns := names(t)
	golden.Compare(t, golden.Spec{Call: "probes:all_templates", Tol: 1e-3, Sample: 120}, func(b *img.BGR) (any, error) {
		out := map[string][][2]any{}
		for _, n := range ns {
			hits := Tpl(n).FindAll(b, Opt{})
			row := make([][2]any, 0, len(hits))
			for _, h := range hits {
				row = append(row, [2]any{h.Rect, round4(h.Score)})
			}
			out[n] = row
		}
		return out, nil
	})
}

func TestButtonStatesMatchPython(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "probes:button_states", Sample: 300}, func(b *img.BGR) (any, error) {
		out := map[string][2]*bool{}
		for _, n := range []string{"InProduce.ButtonUse", "Common.ButtonConfirm", "Common.ButtonNext", "Common.CheckboxUnchecked"} {
			if m := Tpl(n).Find(b, Opt{}); m != nil {
				out[n] = [2]*bool{PrimaryButtonEnabled(b, *m), CheckboxChecked(b, *m)}
			}
		}
		return out, nil
	})
}

var _ = json.Marshal
