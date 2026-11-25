package shell

import (
	"image/png"
	"os"
	"path/filepath"
	"testing"

	"github.com/egoist/mygo/ui"
)

// snap 在设了 HIRO_PREVIEW_OUT 时把当前画面存成 PNG（1280×800 @2x），给人看样子；不设时什么都不做。
func snap(t *testing.T, tt *ui.Tester, name string) {
	dir := os.Getenv("HIRO_PREVIEW_OUT")
	if dir == "" {
		return
	}
	tt.SetScale(2)
	tt.Frame()
	_ = os.MkdirAll(dir, 0o755)
	f, err := os.Create(filepath.Join(dir, name+".png"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := png.Encode(f, tt.Image()); err != nil {
		t.Fatal(err)
	}
}

func TestPreviewShell(t *testing.T) {
	a, _ := newTestApp(newMem(oldThread), &fakeCall{}, nil, viewReg())
	tt := ui.NewTester(a.View, 1280, 800)
	snap(t, tt, "shell-thread")
}
