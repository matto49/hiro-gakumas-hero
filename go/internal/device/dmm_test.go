package device

import (
	"runtime"
	"testing"
)

func TestScreencapFlags(t *testing.T) {
	if v, err := screencapFlags("FramePool|GDI"); err != nil || v != 3 {
		t.Fatalf("%d %v", v, err)
	}
	if v, err := screencapFlags(" FramePool "); err != nil || v != 2 {
		t.Fatalf("%d %v", v, err)
	}
	if _, err := screencapFlags("Nope"); err == nil {
		t.Fatal("unknown name should fail")
	}
	if _, err := screencapFlags(""); err == nil {
		t.Fatal("empty should fail")
	}
}

func TestInputMethodMatchesMaafw5_14(t *testing.T) {
	// maafw 5.14.3：SendMessageWithWindowPos = 128（缺省输入方式）
	if v, err := inputMethod(dmmDefaultInput); err != nil || v != 128 {
		t.Fatalf("%d %v", v, err)
	}
	if _, err := inputMethod("Null"); err == nil {
		t.Fatal("Null is not selectable")
	}
}

func TestIsGameWindow(t *testing.T) {
	if !isGameWindow(" Gakumas ", "UnityWndClass", "gakumas") || isGameWindow("gakumas", "Other", "gakumas") || isGameWindow("x", "UnityWndClass", "gakumas") {
		t.Fatal("window match")
	}
}

func TestFitSize(t *testing.T) {
	if w, h, ok := FitSize(1040, 39); !ok || h != 1001 || w != roundHalfEven(1001*9.0/16) {
		t.Fatalf("%d %d %v", w, h, ok)
	}
	if w, h, ok := FitSize(2200, 40); !ok || w != 1080 || h != 1920 { // 夹到上限
		t.Fatalf("%d %d %v", w, h, ok)
	}
	if _, _, ok := FitSize(900, 40); ok { // 比下限小
		t.Fatal("too small")
	}
}

func TestDMMOutsideWindowsIsUnavailable(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows 上是真驱动")
	}
	d := NewDMM()
	if d.Name() != "maafw-win32" {
		t.Fatal(d.Name())
	}
	if err := d.Connect(); !IsUnavailable(err) {
		t.Fatal(err)
	}
	if d.Alive() {
		t.Fatal("not alive")
	}
}
