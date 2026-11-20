package device

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"strconv"
	"sync"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// Mac + PlayCover：经 hguandl/PlayCover 的 PlayTools 在游戏进程里开的 MaaTools 服务截图、注入触摸（不要求前台、不动鼠标）。
// 协议见 hguandl/PlayTools 的 MaaTools.swift；行为同 Python 版 hiro.devices.maatools。

// MaaToolsPort 是 MaaTools 的缺省端口。
const MaaToolsPort = 1717

const (
	clickHold = 60 * time.Millisecond
	swipeFPS  = 60
	// 游戏以 Catalyst「按 iPad 缩放」运行，UIKit 1pt = 0.77 Mac pt；MaaTools 只把触摸坐标除以 nativeScale(2)，
	// 所以截图物理像素要再除以 0.77 才是 UIKit 坐标 × 2。不除时点击整体偏向左上。
	catalystScale = 0.77
	// 断线（游戏重启、PlayTools 服务重开）后按间隔重连，直到超时；游戏冷启动到 MaaTools 开端口约需十几秒
	reconnectTimeout  = 60 * time.Second
	reconnectInterval = time.Second
	socketTimeout     = 5 * time.Second
	// 空帧（进横屏 LIVE、切全屏时窗口重新布局，MaaTools 会短暂返回空帧）：先重试 emptyRetry；还是空就给一张和上一帧同尺寸的
	// 黑帧（这一拍当没截到）；连续 emptyMax 都是空帧才报 Gone。
	emptyRetry, emptyRetryGap = 3 * time.Second, 300 * time.Millisecond
	emptyMax                  = 30 * time.Second
)

const (
	touchBegan, touchMoved, touchEnded byte = 0, 1, 3
)

var errEmptyFrame = errors.New("MaaTools 返回空帧")

// maaClient 是 MaaTools 协议客户端：截图与触摸，坐标为截图物理像素。
type maaClient struct {
	port              int
	reconnectTimeout  time.Duration
	reconnectInterval time.Duration
	mu                sync.Mutex
	conn              net.Conn
	// 已按下未抬起的触点（发送坐标）。PlayTools 按触点号保存触摸状态且跨连接共享，抬起没送到时该触点会一直占着、
	// 之后的按下全被忽略，所以重连后先补发抬起。
	down *[2]int
}

func (c *maaClient) dial() error {
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(c.port)), socketTimeout)
	if err != nil {
		return err
	}
	_ = conn.SetDeadline(time.Now().Add(socketTimeout))
	if _, err := conn.Write([]byte("MAA\x00")); err != nil {
		_ = conn.Close()
		return err
	}
	ok := make([]byte, 4)
	if _, err := io.ReadFull(conn, ok); err != nil || string(ok) != "OKAY" {
		_ = conn.Close()
		return errors.New("MaaTools 握手失败")
	}
	_ = conn.SetDeadline(time.Time{})
	c.conn = conn
	return nil
}

func (c *maaClient) closeLocked() {
	if c.conn != nil {
		_ = c.conn.Close()
		c.conn = nil
	}
}

func (c *maaClient) send(cmd string, payload []byte) error {
	buf := make([]byte, 2, 2+len(cmd)+len(payload))
	binary.BigEndian.PutUint16(buf, uint16(len(cmd)+len(payload))) //nolint:gosec // 命令很短
	buf = append(append(buf, cmd...), payload...)
	_ = c.conn.SetWriteDeadline(time.Now().Add(socketTimeout))
	_, err := c.conn.Write(buf)
	return err
}

func touchPayload(phase byte, x, y int) []byte {
	p := make([]byte, 6)
	p[0] = phase
	binary.BigEndian.PutUint16(p[1:], uint16(x)) //nolint:gosec // 屏幕坐标
	binary.BigEndian.PutUint16(p[3:], uint16(y)) //nolint:gosec // 同上
	return p
}

// reconnect 按间隔重连直到超时；重连后补发未送达的抬起。
func (c *maaClient) reconnect(reason error) error {
	c.closeLocked()
	slog.Warn("MaaTools 连接断开，重连", "reason", reason, "interval", c.reconnectInterval, "timeout", c.reconnectTimeout)
	t0 := time.Now()
	attempts := 0
	for {
		attempts++
		err := c.dial()
		if err == nil {
			break
		}
		waited := time.Since(t0)
		if waited+c.reconnectInterval > c.reconnectTimeout {
			return fmt.Errorf("MaaTools 重连失败：等了 %.1fs、尝试 %d 次（%w）", waited.Seconds(), attempts, err)
		}
		time.Sleep(c.reconnectInterval)
	}
	slog.Warn("MaaTools 已重连", "waited", time.Since(t0), "attempts", attempts)
	if c.down != nil {
		if err := c.send("TUCH", touchPayload(touchEnded, c.down[0], c.down[1])); err != nil {
			return err
		}
		c.down = nil
	}
	return nil
}

// call 发一条命令；reply 读取响应。连接断开时重连后重发一次。
func (c *maaClient) call(cmd string, payload []byte, reply func(net.Conn) (any, error)) (any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	do := func() (any, error) {
		if err := c.send(cmd, payload); err != nil {
			return nil, err
		}
		if reply == nil {
			return nil, nil
		}
		return reply(c.conn)
	}
	var err error
	if c.conn == nil || !peerAlive(c.conn) {
		err = errors.New("未连接或对端已关闭")
	} else {
		var v any
		v, err = do()
		if err == nil || errors.Is(err, errEmptyFrame) {
			return v, err
		}
	}
	if rerr := c.reconnect(err); rerr != nil {
		return nil, rerr
	}
	return do()
}

func readBGR(conn net.Conn) (any, error) {
	_ = conn.SetReadDeadline(time.Now().Add(socketTimeout * 2))
	defer func() { _ = conn.SetReadDeadline(time.Time{}) }()
	hdr := make([]byte, 12)
	if _, err := io.ReadFull(conn, hdr); err != nil {
		return nil, err
	}
	w, h, n := int(binary.BigEndian.Uint32(hdr)), int(binary.BigEndian.Uint32(hdr[4:])), int(binary.BigEndian.Uint32(hdr[8:]))
	if n == 0 {
		return nil, errEmptyFrame
	}
	if w <= 0 || h <= 0 || n != w*h*3 {
		return nil, fmt.Errorf("MaaTools 帧尺寸不对：%dx%d，%d 字节", w, h, n)
	}
	pix := make([]byte, n)
	if _, err := io.ReadFull(conn, pix); err != nil {
		return nil, err
	}
	return &img.BGR{W: w, H: h, Pix: pix}, nil // 数据本身就是 BGR 顺序
}

func (c *maaClient) screenshot() (*img.BGR, error) {
	v, err := c.call("BGR\x01", nil, readBGR)
	if err != nil {
		return nil, err
	}
	return v.(*img.BGR), nil
}

func (c *maaClient) touch(phase byte, x, y int) error {
	tx, ty := roundHalfEven(float64(x)/catalystScale), roundHalfEven(float64(y)/catalystScale)
	if _, err := c.call("TUCH", touchPayload(phase, tx, ty), nil); err != nil {
		return err
	}
	c.mu.Lock()
	if phase == touchEnded {
		c.down = nil
	} else {
		c.down = &[2]int{tx, ty}
	}
	c.mu.Unlock()
	return nil
}

func (c *maaClient) click(x, y int) error {
	if err := c.touch(touchBegan, x, y); err != nil {
		return err
	}
	time.Sleep(clickHold)
	return c.touch(touchEnded, x, y)
}

func (c *maaClient) swipe(x1, y1, x2, y2 int, duration, hold time.Duration) error {
	if duration <= 0 {
		duration = 300 * time.Millisecond
	}
	steps := max(2, int(duration.Seconds()*swipeFPS))
	if err := c.touch(touchBegan, x1, y1); err != nil {
		return err
	}
	for i := 1; i <= steps; i++ {
		t := float64(i) / float64(steps)
		time.Sleep(time.Second / swipeFPS)
		if err := c.touch(touchMoved, roundHalfEven(float64(x1)+float64(x2-x1)*t), roundHalfEven(float64(y1)+float64(y2-y1)*t)); err != nil {
			return err
		}
	}
	if hold > 0 { // 停在终点再抬起：列表不会带惯性继续滚
		time.Sleep(hold)
		if err := c.touch(touchMoved, x2, y2); err != nil {
			return err
		}
	}
	return c.touch(touchEnded, x2, y2)
}

func (c *maaClient) alive() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.conn != nil && peerAlive(c.conn)
}

// MaaTools 是设备统一接口：Mac + PlayCover。
type MaaTools struct {
	port       int
	client     *maaClient
	screen     Screen
	clock      Clock
	emptySince *time.Duration
	lastSize   [2]int
	grabOnce   func() (*img.BGR, error) // 截一张；空帧返回 nil, nil（测试里换）
}

// NewMaaTools 建一个 MaaTools 设备（不连接）。
func NewMaaTools(port int) *MaaTools {
	d := &MaaTools{
		port: port, clock: RealClock,
		client: &maaClient{port: port, reconnectTimeout: reconnectTimeout, reconnectInterval: reconnectInterval},
	}
	d.grabOnce = d.grabWire
	return d
}

func (d *MaaTools) Name() string        { return "maatools" }
func (d *MaaTools) Orientation() string { return d.screen.Orientation() }

func (d *MaaTools) Connect() error {
	d.client.mu.Lock()
	defer d.client.mu.Unlock()
	if err := d.client.dial(); err != nil {
		return &Error{Kind: Unavailable, Err: err, Msg: fmt.Sprintf("MaaTools 端口 %d 连不上（PlayCover 里开了 maaTools 吗？）：%v", d.port, err)}
	}
	return nil
}

func (d *MaaTools) Close() error {
	d.client.mu.Lock()
	defer d.client.mu.Unlock()
	d.client.closeLocked()
	return nil
}

func (d *MaaTools) grabWire() (*img.BGR, error) {
	f, err := d.client.screenshot()
	if errors.Is(err, errEmptyFrame) {
		return nil, nil
	}
	if err != nil {
		return nil, &Error{Kind: Gone, Err: err, Msg: "MaaTools 断开：" + err.Error()}
	}
	return f, nil
}

func (d *MaaTools) grab() (*img.BGR, error) {
	deadline := d.clock.Now() + emptyRetry
	for {
		f, err := d.grabOnce()
		if err != nil {
			return nil, err
		}
		if f != nil {
			if d.emptySince != nil {
				slog.Info("MaaTools 空帧恢复", "after", d.clock.Now()-*d.emptySince)
			}
			d.emptySince, d.lastSize = nil, [2]int{f.W, f.H}
			return f, nil
		}
		now := d.clock.Now()
		if d.emptySince == nil {
			d.emptySince = &now
			slog.Warn("MaaTools 返回空帧（窗口在重新布局？），重试")
		}
		if now-*d.emptySince >= emptyMax {
			return nil, gone("MaaTools 连续 %.0fs 返回空帧", (now - *d.emptySince).Seconds())
		}
		if now >= deadline {
			break
		}
		d.clock.Sleep(emptyRetryGap)
	}
	if d.lastSize[0] == 0 {
		return nil, unavailable("MaaTools 返回空帧（还没截到过画面）")
	}
	return img.New(d.lastSize[0], d.lastSize[1]), nil // 这一拍当没截到：黑帧
}

// Screenshot 截图 → 逻辑分辨率；进 LIVE 横竖屏切换时重截。
func (d *MaaTools) Screenshot() (*img.BGR, error) {
	return SettledFrame(d.grab, &d.screen, ResizeSettle, d.clock)
}

func (d *MaaTools) native(x, y int) (int, int, error) {
	if w, _ := d.screen.Native(); w == 0 {
		if _, err := d.Screenshot(); err != nil {
			return 0, 0, err
		}
	}
	return d.screen.ToNative(float64(x), float64(y))
}

func (d *MaaTools) Tap(x, y int) error {
	nx, ny, err := d.native(x, y)
	if err != nil {
		return err
	}
	if err := d.client.click(nx, ny); err != nil {
		return &Error{Kind: Gone, Err: err, Msg: "MaaTools 断开：" + err.Error()}
	}
	return nil
}

func (d *MaaTools) Swipe(x1, y1, x2, y2 int, duration, hold time.Duration) error {
	a1, b1, err := d.native(x1, y1)
	if err != nil {
		return err
	}
	a2, b2, err := d.screen.ToNative(float64(x2), float64(y2))
	if err != nil {
		return err
	}
	if err := d.client.swipe(a1, b1, a2, b2, duration, max(0, hold)); err != nil {
		return &Error{Kind: Gone, Err: err, Msg: "MaaTools 断开：" + err.Error()}
	}
	return nil
}

func (d *MaaTools) Alive() bool {
	d.client.mu.Lock()
	connected := d.client.conn != nil
	d.client.mu.Unlock()
	if connected {
		return d.client.alive()
	}
	conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(d.port)), time.Second)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}
