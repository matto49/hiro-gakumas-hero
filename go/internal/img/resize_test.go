package img

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

func TestResizeSameSizeCopies(t *testing.T) {
	src := New(3, 2)
	for i := range src.Pix {
		src.Pix[i] = byte(i)
	}
	dst := Resize(src, 3, 2)
	if !bytes.Equal(dst.Pix, src.Pix) || &dst.Pix[0] == &src.Pix[0] {
		t.Fatal("same size should be a copy")
	}
}

func TestResizeHalfIsAreaAverage(t *testing.T) {
	// 正好缩一半时 OpenCV 改用面积平均：(a+b+c+d+2)>>2
	src := New(2, 2)
	vals := []byte{10, 20, 30, 11, 21, 31, 12, 22, 32, 15, 25, 35}
	copy(src.Pix, vals)
	dst := Resize(src, 1, 1)
	want := []byte{(10 + 11 + 12 + 15 + 2) >> 2, (20 + 21 + 22 + 25 + 2) >> 2, (30 + 31 + 32 + 35 + 2) >> 2}
	if !bytes.Equal(dst.Pix, want) {
		t.Fatalf("%v want %v", dst.Pix, want)
	}
}

func TestCrop(t *testing.T) {
	src := New(4, 3)
	for i := range src.Pix {
		src.Pix[i] = byte(i)
	}
	c := src.Crop(1, 1, 2, 2)
	if c.W != 2 || c.H != 2 || c.Pix[0] != src.Pix[(1*4+1)*3] || c.Pix[3*2] != src.Pix[(2*4+1)*3] {
		t.Fatalf("crop %v", c.Pix)
	}
}

// ---------------------------------------------------------------- 和 OpenCV 对照
//
// cv2.resize 在不同平台上结果不一样：Mac（ARM）走 Carotene / KleidiCV 加速库，x86 一般走 IPP，都不是 OpenCV 自己的通用算法。
// Go 版用通用算法（每个平台一样）。和本机 cv2 比：放大到 720x1280 逐字节相同；缩小最多差 2 个灰度级。

const genScript = `
import sys, numpy as np, cv2
out = sys.argv[1]
cases = [tuple(map(int, c.split("x"))) for c in sys.argv[2:]]
rng = np.random.default_rng(20261120)
for i, (sw, sh, dw, dh) in enumerate(cases):
    src = rng.integers(0, 256, size=(sh, sw, 3), dtype=np.uint8)
    if i % 3 == 1:  # 平滑一点的图：更接近游戏画面
        src = cv2.GaussianBlur(src, (7, 7), 0)
    dst = cv2.resize(src, (dw, dh))
    open(f"{out}/{i}.src", "wb").write(src.tobytes())
    open(f"{out}/{i}.dst", "wb").write(dst.tobytes())
print(cv2.__version__)
`

func python(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("HIRO_PARITY_PYTHON"); p != "" {
		return p
	}
	p, err := pybridge.Python()
	if err != nil {
		t.Skip("没有带 cv2 的 Python（设 HIRO_PARITY_PYTHON）")
	}
	return p
}

func TestResizeMatchesOpenCV(t *testing.T) {
	py := python(t)
	cases := [][4]int{
		{1081, 1921, 720, 1280}, // DMM 客户区
		{541, 961, 720, 1280},   // DMM 小窗口（放大）
		{1440, 2560, 720, 1280}, // 正好一半（面积平均）
		{1170, 2080, 720, 1280},
		{2940, 1654, 1280, 720}, // Mac 全屏横屏裁出 16:9 后
		{1920, 1080, 1280, 720},
		{720, 1280, 360, 640}, // 一半
		{100, 177, 720, 1280},
		{37, 23, 50, 31},
		{50, 31, 37, 23},
		{3, 5, 7, 11},
		{640, 360, 641, 359},
		{17, 17, 16, 16},
		{1000, 1000, 333, 999},
	}
	dir := t.TempDir()
	args := []string{"-c", genScript, dir}
	for _, c := range cases {
		args = append(args, fmt.Sprintf("%dx%dx%dx%d", c[0], c[1], c[2], c[3]))
	}
	out, err := exec.Command(py, args...).CombinedOutput()
	if err != nil {
		t.Skipf("Python 没有 cv2：%v %s", err, out)
	}
	t.Logf("OpenCV %s", bytes.TrimSpace(out))
	for i, c := range cases {
		srcRaw, _ := os.ReadFile(filepath.Join(dir, fmt.Sprintf("%d.src", i)))
		want, _ := os.ReadFile(filepath.Join(dir, fmt.Sprintf("%d.dst", i)))
		src := &BGR{W: c[0], H: c[1], Pix: srcRaw}
		got := Resize(src, c[2], c[3])
		diff, maxd := 0, 0
		for j := range want {
			d := int(got.Pix[j]) - int(want[j])
			if d != 0 {
				diff++
				maxd = max(maxd, d, -d)
			}
		}
		t.Logf("%dx%d → %dx%d：%.2f%% 个字节不一样，最大差 %d", c[0], c[1], c[2], c[3], 100*float64(diff)/float64(len(want)), maxd)
		// 放大到 720x1280（每行 2160 个元素，整 16 个一组）时 Carotene 和 OpenCV 通用算法一样：要逐字节相同
		exact := c[2] == 720 && c[3] == 1280 && c[0] < c[2]
		if exact && diff > 0 {
			t.Errorf("%dx%d → %dx%d：放大应该和 cv2 逐字节相同，差了 %d 个字节", c[0], c[1], c[2], c[3], diff)
		}
		if maxd > 2 {
			t.Errorf("%dx%d → %dx%d：最大差 %d（缩小时允许差 2：cv2 在 Mac 上走 Carotene、x86 上走 IPP，本来就各不相同）", c[0], c[1], c[2], c[3], maxd)
		}
	}
}
