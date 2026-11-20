package device

import (
	"encoding/binary"
	"strings"
	"testing"
	"time"
)

// rawScreencap 造一份 `adb exec-out screencap` 的原始输出：每个像素 RGBA (10,20,30,255)。
func rawScreencap(w, h, header int) []byte {
	out := make([]byte, header, header+w*h*4)
	binary.LittleEndian.PutUint32(out[0:], uint32(w)) //nolint:gosec // 测试尺寸
	binary.LittleEndian.PutUint32(out[4:], uint32(h)) //nolint:gosec // 同上
	binary.LittleEndian.PutUint32(out[8:], 1)
	for range w * h {
		out = append(out, 10, 20, 30, 255)
	}
	return out
}

type fakeAdb struct {
	cmds    [][]string
	offline int
	w, h    int
	header  int
	focus   string
}

func newFakeAdb() *fakeAdb {
	return &fakeAdb{w: 1080, h: 1920, header: 12, focus: GamePackage}
}

func (f *fakeAdb) run(argv []string, _ time.Duration) (int, []byte, []byte) {
	args := argv[1:]
	if len(argv) > 2 && argv[1] == "-s" {
		args = argv[3:]
	}
	f.cmds = append(f.cmds, args)
	if args[0] == "connect" {
		return 0, []byte("already connected to x"), nil
	}
	if f.offline > 0 {
		f.offline--
		return 1, nil, []byte("error: device offline")
	}
	switch {
	case len(args) == 1 && args[0] == "get-state":
		return 0, []byte("device\n"), nil
	case len(args) == 2 && args[0] == "exec-out" && args[1] == "screencap":
		return 0, rawScreencap(f.w, f.h, f.header), nil
	case args[0] == "shell" && strings.Contains(args[1], "dumpsys window"):
		return 0, []byte("  mCurrentFocus=Window{abc u0 " + f.focus + "/.Main}\n"), nil
	}
	return 0, nil, nil
}

func TestParseScreencapHandlesBothHeadersAndConvertsToBGR(t *testing.T) {
	for _, header := range []int{12, 16} {
		m, err := ParseScreencap(rawScreencap(4, 6, header))
		if err != nil || m.W != 4 || m.H != 6 || m.Pix[0] != 30 || m.Pix[1] != 20 || m.Pix[2] != 10 {
			t.Fatalf("header %d: %v %v", header, m, err)
		}
	}
	if _, err := ParseScreencap(make([]byte, 8)); !IsUnavailable(err) {
		t.Fatal(err)
	}
}

func TestAdbTapSwipeAndAlive(t *testing.T) {
	f := newFakeAdb()
	d := NewAdb("127.0.0.1:16384", "adb")
	d.run = f.run
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	if s, err := d.Screenshot(); err != nil || s.W != 720 || s.H != 1280 {
		t.Fatalf("%v %v", s, err)
	}
	last := func() string { return strings.Join(f.cmds[len(f.cmds)-1], "|") }
	if err := d.Tap(360, 640); err != nil || last() != "shell|input tap 540 960" {
		t.Fatalf("%v %s", err, last())
	}
	if err := d.Swipe(100, 900, 100, 300, 300*time.Millisecond, 0); err != nil || last() != "shell|input swipe 150 1350 150 450 300" {
		t.Fatalf("%v %s", err, last())
	}
	if err := d.Swipe(100, 900, 100, 300, 300*time.Millisecond, 300*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	script := f.cmds[len(f.cmds)-1][1]
	if !strings.HasPrefix(script, "input motionevent DOWN 150 1350") || !strings.Contains(script, "sleep 0.30") || !strings.HasSuffix(script, "input motionevent UP 150 450") {
		t.Fatalf("%q", script)
	}
	if !d.Alive() {
		t.Fatal("alive")
	}
	f.focus = "com.android.launcher3"
	if d.Alive() {
		t.Fatal("not alive when another app is in front")
	}
}

func TestAdbReconnectsThenGivesUp(t *testing.T) {
	f := newFakeAdb()
	d := NewAdb("127.0.0.1:16384", "adb")
	d.run = f.run
	f.offline = 1
	if _, err := d.Screenshot(); err != nil { // 掉线一次：adb connect 后成功
		t.Fatal(err)
	}
	found := false
	for _, c := range f.cmds {
		found = found || strings.Join(c, " ") == "connect 127.0.0.1:16384"
	}
	if !found {
		t.Fatal("should adb connect")
	}
	f.offline = 2
	if _, err := d.Screenshot(); !IsGone(err) {
		t.Fatal(err)
	}
}

func TestAdbConnectRefusesBadState(t *testing.T) {
	d := NewAdb("127.0.0.1:16384", "adb")
	d.run = func(argv []string, _ time.Duration) (int, []byte, []byte) {
		for _, a := range argv {
			if a == "connect" {
				return 0, []byte("already connected"), nil
			}
		}
		return 1, nil, []byte("error: device offline")
	}
	if err := d.Connect(); !IsUnavailable(err) {
		t.Fatal(err)
	}
}

func TestFocusPackage(t *testing.T) {
	if FocusPackage("mCurrentFocus=Window{1 u0 com.a.b/.X}") != "com.a.b" || FocusPackage("mCurrentFocus=null") != "" {
		t.Fatal("focus")
	}
}

func TestAdbShell(t *testing.T) {
	f := newFakeAdb()
	d := NewAdb("127.0.0.1:16384", "adb")
	d.run = f.run
	out, err := d.Shell("dumpsys window | grep mCurrentFocus", 15*time.Second)
	if err != nil || FocusPackage(out) != GamePackage || strings.Join(f.cmds[len(f.cmds)-1], "|") != "shell|dumpsys window | grep mCurrentFocus" {
		t.Fatalf("%q %v", out, err)
	}
}
