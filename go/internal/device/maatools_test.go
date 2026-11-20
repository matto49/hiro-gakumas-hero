package device

import (
	"encoding/binary"
	"errors"
	"io"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// fakeMaaTools 模拟 PlayTools 的 MaaTools 服务：握手 MAA\0 → OKAY；BGR\x01 → 宽高长度 + 像素；TUCH 记下来。
type fakeMaaTools struct {
	ln      net.Listener
	w, h    int
	mu      sync.Mutex
	touches [][4]int
	empty   int // 接下来几次截图回空帧
	conns   []net.Conn
}

func newFakeMaaTools(t *testing.T, w, h int) *fakeMaaTools {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeMaaTools{ln: ln, w: w, h: h}
	go f.serve()
	t.Cleanup(func() { f.close() })
	return f
}

func (f *fakeMaaTools) port() int { return f.ln.Addr().(*net.TCPAddr).Port }

func (f *fakeMaaTools) close() {
	_ = f.ln.Close()
	f.mu.Lock()
	for _, c := range f.conns {
		_ = c.Close()
	}
	f.mu.Unlock()
}

// dropClients 断开现有连接（模拟游戏重启）。
func (f *fakeMaaTools) dropClients() {
	f.mu.Lock()
	for _, c := range f.conns {
		_ = c.Close()
	}
	f.conns = nil
	f.mu.Unlock()
}

func (f *fakeMaaTools) serve() {
	for {
		c, err := f.ln.Accept()
		if err != nil {
			return
		}
		f.mu.Lock()
		f.conns = append(f.conns, c)
		f.mu.Unlock()
		go f.handle(c)
	}
}

func (f *fakeMaaTools) handle(c net.Conn) {
	defer c.Close()
	hello := make([]byte, 4)
	if _, err := io.ReadFull(c, hello); err != nil || string(hello) != "MAA\x00" {
		return
	}
	_, _ = c.Write([]byte("OKAY"))
	for {
		var n uint16
		if err := binary.Read(c, binary.BigEndian, &n); err != nil {
			return
		}
		body := make([]byte, n)
		if _, err := io.ReadFull(c, body); err != nil {
			return
		}
		switch string(body[:4]) {
		case "BGR\x01":
			f.mu.Lock()
			empty := f.empty > 0
			if empty {
				f.empty--
			}
			f.mu.Unlock()
			pix := []byte{}
			if !empty {
				pix = make([]byte, f.w*f.h*3)
				for i := range pix {
					pix[i] = 128
				}
			}
			hdr := make([]byte, 12)
			binary.BigEndian.PutUint32(hdr[0:], uint32(f.w))      //nolint:gosec // 测试尺寸很小
			binary.BigEndian.PutUint32(hdr[4:], uint32(f.h))      //nolint:gosec // 同上
			binary.BigEndian.PutUint32(hdr[8:], uint32(len(pix))) //nolint:gosec // 测试尺寸
			_, _ = c.Write(append(hdr, pix...))
		case "TUCH":
			f.mu.Lock()
			f.touches = append(f.touches, [4]int{int(body[4]), int(binary.BigEndian.Uint16(body[5:])), int(binary.BigEndian.Uint16(body[7:])), int(body[9])})
			f.mu.Unlock()
		}
	}
}

func (f *fakeMaaTools) waitTouches(t *testing.T, n int) [][4]int {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		f.mu.Lock()
		got := append([][4]int(nil), f.touches...)
		f.mu.Unlock()
		if len(got) >= n {
			return got
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("等不到 %d 个触摸", n)
	return nil
}

func TestMaaToolsEndToEnd(t *testing.T) {
	srv := newFakeMaaTools(t, 1170, 2080)
	d := NewMaaTools(srv.port())
	d.client.reconnectTimeout, d.client.reconnectInterval = time.Second, 100*time.Millisecond
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	shot, err := d.Screenshot()
	if err != nil || shot.W != 720 || shot.H != 1280 || d.Name() != "maatools" {
		t.Fatalf("%v %v", shot, err)
	}
	if err := d.Tap(360, 640); err != nil {
		t.Fatal(err)
	}
	got := srv.waitTouches(t, 2)
	// 逻辑 → 截图像素（1170/720 = 1.625）→ 除以 Catalyst 的 0.77
	if got[0][0] != int(touchBegan) || got[1][0] != int(touchEnded) {
		t.Fatalf("phases %v", got)
	}
	if got[0][1] != roundHalfEven(585/0.77) || got[0][2] != roundHalfEven(1040/0.77) {
		t.Fatalf("coords %v", got[0])
	}
	if !d.Alive() {
		t.Fatal("alive")
	}
}

func TestMaaToolsSwipeHoldsAtTheEnd(t *testing.T) {
	srv := newFakeMaaTools(t, 720, 1280)
	d := NewMaaTools(srv.port())
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if _, err := d.Screenshot(); err != nil {
		t.Fatal(err)
	}
	if err := d.Swipe(100, 900, 100, 300, 100*time.Millisecond, 50*time.Millisecond); err != nil {
		t.Fatal(err)
	}
	steps := max(2, int(0.1*swipeFPS))
	got := srv.waitTouches(t, 1+steps+2)
	last := got[len(got)-1]
	before := got[len(got)-2]
	if got[0][0] != int(touchBegan) || last[0] != int(touchEnded) || before[0] != int(touchMoved) {
		t.Fatalf("%v", got)
	}
	if before[2] != roundHalfEven(300/catalystScale) || last[2] != roundHalfEven(300/catalystScale) {
		t.Fatalf("end %v %v", before, last)
	}
}

func TestMaaToolsConnectErrorsAreUnavailable(t *testing.T) {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	port := ln.Addr().(*net.TCPAddr).Port
	_ = ln.Close()
	d := NewMaaTools(port)
	if err := d.Connect(); !IsUnavailable(err) {
		t.Fatalf("%v", err)
	}
	if d.Alive() {
		t.Fatal("not alive")
	}
}

func TestMaaToolsReconnectsAndReleasesTheStuckTouch(t *testing.T) {
	srv := newFakeMaaTools(t, 720, 1280)
	d := NewMaaTools(srv.port())
	d.client.reconnectTimeout, d.client.reconnectInterval = 2*time.Second, 50*time.Millisecond
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if _, err := d.Screenshot(); err != nil {
		t.Fatal(err)
	}
	d.client.down = &[2]int{11, 22} // 按下没抬起时连接断了
	srv.dropClients()
	time.Sleep(50 * time.Millisecond)
	if _, err := d.Screenshot(); err != nil { // 发现断开 → 重连 → 先补发抬起 → 截图
		t.Fatal(err)
	}
	got := srv.waitTouches(t, 1)
	if got[0] != [4]int{int(touchEnded), 11, 22, 0} || d.client.down != nil {
		t.Fatalf("%v", got)
	}
}

func TestMaaToolsGivesUpAfterReconnectTimeout(t *testing.T) {
	srv := newFakeMaaTools(t, 720, 1280)
	d := NewMaaTools(srv.port())
	d.client.reconnectTimeout, d.client.reconnectInterval = 200*time.Millisecond, 50*time.Millisecond
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	srv.close()
	time.Sleep(50 * time.Millisecond)
	if _, err := d.Screenshot(); !IsGone(err) {
		t.Fatalf("%v", err)
	}
}

// 11-17 Mac p15c：刚切全屏时 MaaTools 回空帧。短暂空帧重试；一直空就给上一帧尺寸的黑帧；连续 30 秒才报断开。
func TestMaaToolsEmptyFramesRetryThenBlackThenGone(t *testing.T) {
	now := time.Duration(0)
	d := NewMaaTools(1)
	d.clock = Clock{Now: func() time.Duration { return now }, Sleep: func(x time.Duration) { now += x }}
	var seq []*img.BGR
	d.grabOnce = func() (*img.BGR, error) {
		if len(seq) == 0 {
			return nil, nil
		}
		f := seq[0]
		seq = seq[1:]
		return f, nil
	}
	if _, err := d.Screenshot(); !IsUnavailable(err) { // 还没截到过画面：不知道尺寸
		t.Fatal(err)
	}
	seq = []*img.BGR{bgr(720, 1280, 128)}
	if s, err := d.Screenshot(); err != nil || s.W != 720 {
		t.Fatal(err)
	}
	seq = []*img.BGR{nil, nil, bgr(1280, 720, 128)}
	if s, err := d.Screenshot(); err != nil || s.Pix[0] != 128 || d.emptySince != nil {
		t.Fatalf("%v %v", s, err)
	}
	seq = nil
	s, err := d.Screenshot()
	if err != nil || s.AnyNonZero() || d.emptySince == nil {
		t.Fatalf("black frame %v %v", s, err)
	}
	now += emptyMax
	if _, err := d.Screenshot(); !IsGone(err) {
		t.Fatal(err)
	}
}

func TestMaaToolsEmptyFrameOnTheWire(t *testing.T) {
	srv := newFakeMaaTools(t, 720, 1280)
	d := NewMaaTools(srv.port())
	if err := d.Connect(); err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	srv.mu.Lock()
	srv.empty = 1
	srv.mu.Unlock()
	f, err := d.grabOnce()
	if err != nil || f != nil {
		t.Fatalf("empty frame should be nil, nil: %v %v", f, err)
	}
	if f, err := d.grabOnce(); err != nil || f == nil {
		t.Fatalf("%v %v", f, err)
	}
	if !errors.Is(errEmptyFrame, errEmptyFrame) {
		t.Fatal("sentinel")
	}
}
