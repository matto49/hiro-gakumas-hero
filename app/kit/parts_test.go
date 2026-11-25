package kit

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/egoist/mygo/ui"
)

func TestSourceTagShowsChineseLabel(t *testing.T) {
	tt := ui.NewTester(func(c *ui.Context) {
		ui.Row(c).Children(func() {
			SourceTag(c, "gnrpa")
			SourceTag(c, "deepseek")
			StatePill(c, "running")
		})
	}, 400, 60)
	for _, s := range []string{"算法", "LLM", "游玩中"} {
		if !tt.HasText(s) {
			t.Errorf("没有 %q：%q", s, tt.Texts())
		}
	}
}

func TestPrimaryButtonClicks(t *testing.T) {
	n := 0
	tt := ui.NewTester(func(c *ui.Context) {
		if Primary(c, "开始").Clicked() {
			n++
		}
	}, 200, 60)
	if err := tt.Click("开始"); err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Errorf("点了 %d 次", n)
	}
}

// I8：位图缓存有上限，长局截图不会一直占内存。
func TestBitmapCacheIsBounded(t *testing.T) {
	dir := t.TempDir()
	png1 := []byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n', 0, 0, 0, 13, 'I', 'H', 'D', 'R', 0, 0, 0, 1, 0, 0, 0, 1, 8, 6, 0, 0, 0, 0x1f, 0x15, 0xc4, 0x89, 0, 0, 0, 13, 'I', 'D', 'A', 'T', 0x78, 0x9c, 0x63, 0xf8, 0xcf, 0xc0, 0xf0, 0x1f, 0, 0x05, 0x00, 0x01, 0xff, 0x89, 0x99, 0x3d, 0x1d, 0, 0, 0, 0, 'I', 'E', 'N', 'D', 0xae, 0x42, 0x60, 0x82}
	for i := 0; i < MaxBitmaps+40; i++ {
		p := filepath.Join(dir, fmt.Sprintf("%d.png", i))
		_ = os.WriteFile(p, png1, 0o644)
		Bitmap(p)
	}
	if n := bitmapCount(); n > MaxBitmaps {
		t.Errorf("缓存了 %d 张，上限 %d", n, MaxBitmaps)
	}
}
