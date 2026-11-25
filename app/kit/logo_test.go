package kit

import (
	"testing"

	"github.com/egoist/mygo/ui"
)

func TestLogoDecodesAndDraws(t *testing.T) {
	if LogoBitmap() == nil {
		t.Fatal("内嵌的 logo 解不出来")
	}
	tt := ui.NewTester(func(c *ui.Context) { ui.Row(c).Children(func() { Logo(c, 24) }) }, 100, 40)
	if _, ok := tt.Access("初星助手"); !ok {
		t.Error("logo 要有名字给读屏")
	}
}
