package pil

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// 和 Pillow 对照：随机图按几组尺寸缩放（放大、缩小、只缩一个方向），再转灰度，结果要逐字节一样。
const script = `
import sys, numpy as np
from PIL import Image
out = sys.argv[1]
rng = np.random.default_rng(3)
rgb = rng.integers(0, 256, size=(37, 53, 3), dtype=np.uint8)
gray = rng.integers(0, 256, size=(29, 71), dtype=np.uint8)
for i, (w, h) in enumerate([(112, 64), (20, 64), (53, 64), (90, 17), (7, 5)]):
    a = Image.fromarray(rgb, "RGB").resize((w, h), Image.LANCZOS)
    open(f"{out}/rgb{i}", "wb").write(np.asarray(a).tobytes())
    open(f"{out}/l{i}", "wb").write(np.asarray(a.convert("L")).tobytes())
    b = Image.fromarray(gray, "L").resize((w, h), Image.LANCZOS)
    open(f"{out}/g{i}", "wb").write(np.asarray(b).tobytes())
open(f"{out}/src_rgb", "wb").write(rgb.tobytes())
open(f"{out}/src_gray", "wb").write(gray.tobytes())
`

func TestMatchesPillow(t *testing.T) {
	py := os.Getenv("HIRO_PARITY_PYTHON")
	if py == "" {
		t.Skip("设 HIRO_PARITY_PYTHON（装了 Pillow 的 Python）")
	}
	dir := t.TempDir()
	if out, err := exec.Command(py, "-c", script, dir).CombinedOutput(); err != nil { //nolint:gosec // 测试：本机配置的 Python
		t.Skipf("Pillow 跑不了：%v %s", err, out)
	}
	read := func(n string) []byte {
		b, err := os.ReadFile(filepath.Join(dir, n))
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	rgb := &Image{W: 53, H: 37, Bands: 3, Pix: read("src_rgb")}
	gray := &Image{W: 71, H: 29, Bands: 1, Pix: read("src_gray")}
	for i, sz := range [][2]int{{112, 64}, {20, 64}, {53, 64}, {90, 17}, {7, 5}} {
		a := Resize(rgb, sz[0], sz[1])
		if !bytes.Equal(a.Pix, read("rgb"+string(rune('0'+i)))) {
			t.Errorf("RGB %v 不一样", sz)
		}
		if !bytes.Equal(ToL(a).Pix, read("l"+string(rune('0'+i)))) {
			t.Errorf("L %v 不一样", sz)
		}
		if !bytes.Equal(Resize(gray, sz[0], sz[1]).Pix, read("g"+string(rune('0'+i)))) {
			t.Errorf("灰度 %v 不一样", sz)
		}
	}
}
