package kit

import (
	_ "embed"
	"sync"

	"github.com/egoist/mygo/ui"
)

// logo.png 是应用图标（icon/hiro-mark.png）缩到 128 的那张：橙底白色探头的广 + 星星。
//
//go:embed logo.png
var logoPNG []byte

var (
	logoOnce sync.Once
	logoBmp  *ui.Bitmap
)

// LogoBitmap 是初星助手的标志图（解一次，之后复用）。
func LogoBitmap() *ui.Bitmap {
	logoOnce.Do(func() { logoBmp, _ = ui.DecodeBitmap(logoPNG) })
	return logoBmp
}

// Logo 画边长 size 的圆角标志（侧栏、第一次使用、新对话页的兜底卡面）。
func Logo(c *ui.Context, size float32) ui.Element {
	if b := LogoBitmap(); b != nil {
		return ui.Image(c, b).Size(size, size).Radius(size * 0.26).Label("初星助手").Shrink(0)
	}
	return ui.Box(c).Size(size, size).Radius(size * 0.26).Background(Brand).Label("初星助手").Shrink(0)
}
