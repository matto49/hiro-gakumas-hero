package device

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

// MuMu / 雷电 / 真机等安卓设备：直接调 adb 命令，行为同 Python 版 hiro.devices.adb。
//
//	HIRO_ADB       adb 地址 ip:port（缺省 127.0.0.1:5555；MuMu 12 一般是 127.0.0.1:16384），也可以是 USB 序列号
//	HIRO_ADB_PATH  adb 可执行文件（缺省 PATH 里的 adb）
//
// 截图：`adb exec-out screencap`（不压 PNG，原始 RGBA）；点击 `input tap`；滑动 hold = 0 用 `input swipe … 毫秒`，
// hold > 0 用一条 shell 串起 `input motionevent DOWN / MOVE / UP`（安卓 10 起），到终点停住再松手。

// GamePackage 是学マス的包名。
const GamePackage = "com.bandainamcoent.idolmaster_gakuen"

const defaultSerial = "127.0.0.1:5555"

var (
	goneWords    = []string{"offline", "not found", "no devices", "unauthorized", "closed", "cannot connect", "failed to connect"}
	focusPattern = regexp.MustCompile(`mCurrentFocus=Window\{\S+ \S+ ([\w.]+)/`)
)

// FocusPackage 「mCurrentFocus=Window{… u0 包名/Activity}」→ 包名；没有返回 ""。
func FocusPackage(text string) string {
	if m := focusPattern.FindStringSubmatch(text); m != nil {
		return m[1]
	}
	return ""
}

// ParseScreencap `screencap` 的原始输出 → BGR。头是 宽、高、格式（小端 uint32），安卓 12 起再多一个色彩空间。
func ParseScreencap(data []byte) (*img.BGR, error) {
	if len(data) < 12 {
		return nil, unavailable("screencap 输出太短（%d 字节）", len(data))
	}
	w, h, format := int(binary.LittleEndian.Uint32(data)), int(binary.LittleEndian.Uint32(data[4:])), binary.LittleEndian.Uint32(data[8:])
	n := w * h * 4
	header := len(data) - n
	if w <= 0 || h <= 0 || (header != 12 && header != 16) || (format != 1 && format != 2) { // 1 RGBA_8888，2 RGBX_8888
		return nil, unavailable("screencap 格式不认识：%dx%d 格式 %d，头 %d 字节", w, h, format, header)
	}
	return img.FromRGBA(w, h, data[header:])
}

// Adb 是设备统一接口：安卓设备 / 模拟器。
type Adb struct {
	serial string
	adb    string
	screen Screen
	clock  Clock
	// run 跑一条命令，返回 (退出码, stdout, stderr)；测试里换成假的。
	run func(argv []string, timeout time.Duration) (int, []byte, []byte)
}

// NewAdb 建一个 adb 设备（不连接）。serial / adb 空就用环境变量或缺省值。
func NewAdb(serial, adb string) *Adb {
	if serial == "" {
		serial = os.Getenv("HIRO_ADB")
	}
	if serial == "" {
		serial = defaultSerial
	}
	if adb == "" {
		adb = os.Getenv("HIRO_ADB_PATH")
	}
	if adb == "" {
		if p, err := exec.LookPath("adb"); err == nil {
			adb = p
		} else {
			adb = "adb"
		}
	}
	return &Adb{serial: serial, adb: adb, clock: RealClock, run: runCommand}
}

// run 的特殊退出码：找不到 adb、超时。
const (
	errNoAdb   = -127
	errTimeout = -128
)

func runCommand(argv []string, timeout time.Duration) (int, []byte, []byte) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	proc.Hide(cmd)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	err := cmd.Run()
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return errTimeout, out.Bytes(), nil
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), out.Bytes(), errb.Bytes()
	}
	if err != nil {
		return errNoAdb, nil, []byte(err.Error())
	}
	return 0, out.Bytes(), errb.Bytes()
}

func (d *Adb) Name() string        { return "adb" }
func (d *Adb) Orientation() string { return d.screen.Orientation() }

func (d *Adb) networked() bool { return strings.Contains(d.serial, ":") }

func (d *Adb) cmd(timeout time.Duration, args ...string) ([]byte, error) {
	argv := append([]string{d.adb, "-s", d.serial}, args...)
	for attempt := range 2 {
		code, out, errb := d.run(argv, timeout)
		if code == 0 {
			return out, nil
		}
		if code == errTimeout {
			return nil, gone("adb 超时（%s）：%s", timeout, clip(strings.Join(args, " "), 80))
		}
		if code == errNoAdb {
			return nil, unavailable("找不到 adb：%s（装 platform-tools，或设 HIRO_ADB_PATH）", d.adb)
		}
		text := strings.ToLower(strings.TrimSpace(string(errb)))
		isGone := false
		for _, w := range goneWords {
			isGone = isGone || strings.Contains(text, w)
		}
		if isGone && attempt == 0 && d.networked() {
			d.run([]string{d.adb, "connect", d.serial}, 10*time.Second)
			continue
		}
		if isGone {
			return nil, gone("adb 设备 %s 不在了：%s", d.serial, clip(text, 200))
		}
		return nil, unavailable("adb %s 失败（%d）：%s", clip(strings.Join(args, " "), 80), code, clip(text, 200))
	}
	return nil, gone("adb 设备 %s 重连后还是不行", d.serial)
}

func clip(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func (d *Adb) Connect() error {
	if d.networked() {
		code, out, errb := d.run([]string{d.adb, "connect", d.serial}, 10*time.Second)
		text := strings.ToLower(string(out) + string(errb))
		if code != 0 || (!strings.Contains(text, "connected") && !strings.Contains(text, "already")) {
			return unavailable("adb connect %s 失败：%s", d.serial, clip(strings.TrimSpace(text), 200))
		}
	}
	code, out, errb := d.run([]string{d.adb, "-s", d.serial, "get-state"}, 10*time.Second)
	state := strings.TrimSpace(string(out))
	if code != 0 || state != "device" {
		if state == "" {
			state = strings.TrimSpace(string(errb))
		}
		return unavailable("adb 设备 %s 状态 %s", d.serial, state)
	}
	_, err := d.Screenshot()
	return err
}

// Close 不 adb disconnect：别的程序可能也在用这台设备。
func (d *Adb) Close() error { return nil }

// Shell 跑一条 adb shell 命令，返回输出（不在统一接口里：查前台应用、闪退后拉起游戏用）。
func (d *Adb) Shell(cmd string, timeout time.Duration) (string, error) {
	out, err := d.cmd(timeout, "shell", cmd)
	return strings.ToValidUTF8(string(out), "�"), err
}

func (d *Adb) Alive() bool {
	out, err := d.Shell("dumpsys window | grep mCurrentFocus", 15*time.Second)
	return err == nil && FocusPackage(out) == GamePackage
}

// Screenshot 截图 → 逻辑分辨率；进 LIVE 横竖屏切换时重截。
func (d *Adb) Screenshot() (*img.BGR, error) {
	return SettledFrame(func() (*img.BGR, error) {
		raw, err := d.cmd(15*time.Second, "exec-out", "screencap")
		if err != nil {
			return nil, err
		}
		return ParseScreencap(raw)
	}, &d.screen, ResizeSettle, d.clock)
}

func (d *Adb) native(x, y int) (int, int, error) {
	if w, _ := d.screen.Native(); w == 0 {
		if _, err := d.Screenshot(); err != nil {
			return 0, 0, err
		}
	}
	return d.screen.ToNative(float64(x), float64(y))
}

func (d *Adb) Tap(x, y int) error {
	nx, ny, err := d.native(x, y)
	if err != nil {
		return err
	}
	_, err = d.cmd(15*time.Second, "shell", fmt.Sprintf("input tap %d %d", nx, ny))
	return err
}

func (d *Adb) Swipe(x1, y1, x2, y2 int, duration, hold time.Duration) error {
	a1, b1, err := d.native(x1, y1)
	if err != nil {
		return err
	}
	a2, b2, err := d.screen.ToNative(float64(x2), float64(y2))
	if err != nil {
		return err
	}
	if duration <= 0 {
		duration = 300 * time.Millisecond
	}
	if hold <= 0 {
		ms := max(1, roundHalfEven(duration.Seconds()*1000))
		_, err = d.cmd(15*time.Second, "shell", fmt.Sprintf("input swipe %d %d %d %d %d", a1, b1, a2, b2, ms))
		return err
	}
	steps := max(2, min(8, roundHalfEven(duration.Seconds()/0.1)))
	parts := []string{fmt.Sprintf("input motionevent DOWN %d %d", a1, b1)}
	for i := 1; i <= steps; i++ {
		t := float64(i) / float64(steps)
		parts = append(parts, fmt.Sprintf("input motionevent MOVE %d %d", roundHalfEven(float64(a1)+float64(a2-a1)*t), roundHalfEven(float64(b1)+float64(b2-b1)*t)))
	}
	parts = append(parts, fmt.Sprintf("sleep %.2f", hold.Seconds()), fmt.Sprintf("input motionevent MOVE %d %d", a2, b2), fmt.Sprintf("input motionevent UP %d %d", a2, b2))
	_, err = d.cmd(15*time.Second+duration+hold, "shell", strings.Join(parts, "; "))
	return err
}
