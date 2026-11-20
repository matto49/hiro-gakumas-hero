package device

import (
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

func bgr(w, h int, v byte) *img.BGR {
	b := img.New(w, h)
	for i := range b.Pix {
		b.Pix[i] = v
	}
	return b
}

func TestLogicalSizeFollowsOrientation(t *testing.T) {
	if w, h := LogicalSize(1081, 1921); w != 720 || h != 1280 {
		t.Fatal("portrait")
	}
	if w, h := LogicalSize(1921, 1081); w != 1280 || h != 720 {
		t.Fatal("landscape")
	}
}

func TestFrameStretchesAndTapsMapPerAxis(t *testing.T) {
	var s Screen
	out, err := s.Frame(bgr(1081, 1921, 128))
	if err != nil || out.W != 720 || out.H != 1280 || s.Orientation() != Portrait {
		t.Fatalf("%v %v", out, err)
	}
	check := func(x, y float64, wx, wy int) {
		t.Helper()
		nx, ny, err := s.ToNative(x, y)
		if err != nil || nx != wx || ny != wy {
			t.Fatalf("ToNative(%v,%v) = %d,%d %v want %d,%d", x, y, nx, ny, err, wx, wy)
		}
	}
	check(360, 640, roundHalfEven(360*1081.0/720), roundHalfEven(640*1921.0/1280)) // 各按各的比例
	check(720, 1280, 1080, 1920)                                                   // 右下角夹在画面内
	check(0, 0, 0, 0)
}

func TestLandscapeFrameAndTaps(t *testing.T) {
	var s Screen
	out, _ := s.Frame(bgr(1921, 1081, 1))
	if out.W != 1280 || out.H != 720 || s.Orientation() != Landscape {
		t.Fatal("landscape")
	}
	if nx, ny, _ := s.ToNative(1280, 0); nx != 1920 || ny != 0 {
		t.Fatal(nx, ny)
	}
}

func TestMacFullscreenLandscapeIsCroppedTo16x9(t *testing.T) {
	var s Screen
	out, err := s.Frame(bgr(2940, 1838, 1)) // PlayCover 横屏 LIVE 全屏
	if err != nil || out.W != 1280 || out.H != 720 {
		t.Fatalf("%v %v", out, err)
	}
	ch := roundHalfEven(2940 * 9.0 / 16)
	if nx, ny, _ := s.ToNative(0, 0); nx != 0 || ny != (1838-ch)/2 {
		t.Fatalf("crop offset %d,%d", nx, ny)
	}
}

func TestNativeSizeFollowsEachScreenshot(t *testing.T) {
	var s Screen
	_, _ = s.Frame(bgr(720, 1280, 1))
	if nx, ny, _ := s.ToNative(100, 200); nx != 100 || ny != 200 {
		t.Fatal(nx, ny)
	}
	_, _ = s.Frame(bgr(1440, 2560, 1)) // 窗口被拉大
	if nx, ny, _ := s.ToNative(100, 200); nx != 200 || ny != 400 {
		t.Fatal(nx, ny)
	}
}

func TestBadAspectIsRefused(t *testing.T) {
	if err := CheckAspect(1000, 1000); !IsUnavailable(err) {
		t.Fatal(err)
	}
	var s Screen
	if _, err := s.Frame(bgr(800, 1000, 1)); !IsUnavailable(err) {
		t.Fatal(err)
	}
	var fresh Screen
	if _, _, err := fresh.ToNative(1, 1); !IsUnavailable(err) { // 没截过图
		t.Fatal(err)
	}
}

func TestSettledFrameRetriesWhileResizing(t *testing.T) {
	now := time.Duration(0)
	var slept []time.Duration
	clock := Clock{
		Now:   func() time.Duration { return now },
		Sleep: func(d time.Duration) { slept = append(slept, d); now += d },
	}
	frames := []*img.BGR{bgr(540, 572, 1), bgr(540, 572, 1), bgr(961, 541, 1)}
	grab := func() (*img.BGR, error) { f := frames[0]; frames = frames[1:]; return f, nil }
	out, err := SettledFrame(grab, &Screen{}, ResizeSettle, clock)
	if err != nil || out.W != 1280 || out.H != 720 || len(slept) != 2 {
		t.Fatalf("%v %v %d", out, err, len(slept))
	}
	out, err = SettledFrame(func() (*img.BGR, error) { return bgr(541, 961, 1), nil }, &Screen{}, ResizeSettle, clock)
	if err != nil || out.W != 720 {
		t.Fatal(err)
	}
	if _, err := SettledFrame(func() (*img.BGR, error) { return bgr(540, 572, 1), nil }, &Screen{}, time.Second, clock); !IsUnavailable(err) {
		t.Fatal(err)
	}
}

func TestOpenByName(t *testing.T) {
	for name, want := range map[string]string{"maatools": "maatools", "dmm": "maafw-win32", "adb": "adb", "mumu": "adb", "leidian": "adb"} {
		d, err := Open(name)
		if err != nil || d.Name() != want {
			t.Fatalf("%s: %v %v", name, d, err)
		}
	}
	if _, err := Open("quartz"); err == nil {
		t.Fatal("quartz should be refused")
	}
	t.Setenv("HIRO_DEVICE", "mumu")
	if d, err := Open(""); err != nil || d.Name() != "adb" {
		t.Fatal(err)
	}
}
