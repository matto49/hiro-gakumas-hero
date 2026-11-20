package cv

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

// 和 Python 的 cv2 对照：同一张随机图，同样的操作，结果要一样（浮点分数差 1e-4 以内）。
// 用跑实机的那个 Python（HIRO_PARITY_PYTHON；缺省 pybridge 找到的）。

const opsScript = `
import sys, json, numpy as np, cv2
out = sys.argv[1]
rng = np.random.default_rng(7)
src = cv2.GaussianBlur(rng.integers(0, 256, size=(200, 160, 3), dtype=np.uint8), (5, 5), 0)
src[40:90, 30:120] = (250, 250, 250)   # 一块白色：连通块、轮廓、阈值都有东西
src[120:150, 60:100] = (20, 160, 230)  # 一块橙色：HSV / inRange
gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
tpl = gray[50:80, 40:90].copy()
def save(name, a): open(f"{out}/{name}", "wb").write(np.ascontiguousarray(a).tobytes())
save("src", src)
save("gray", gray)
save("hsv", cv2.cvtColor(src, cv2.COLOR_BGR2HSV))
save("cubic", cv2.resize(src, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
save("area", cv2.resize(src, (80, 100), interpolation=cv2.INTER_AREA))
save("inrange", cv2.inRange(cv2.cvtColor(src, cv2.COLOR_BGR2HSV), np.array((10, 50, 50)), np.array((25, 255, 255))))
save("thresh", cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)[1])
save("thresh_bgr", cv2.threshold(src, 127, 255, cv2.THRESH_BINARY)[1])
t, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
save("otsu", otsu)
k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
bw = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)[1]
save("dilate", cv2.dilate(bw, k, iterations=2))
save("erode", cv2.erode(bw, k, iterations=1))
save("close", cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k))
save("canny", cv2.Canny(cv2.threshold(src, 190, 255, cv2.THRESH_BINARY)[1], 200, 250))
save("border", cv2.copyMakeBorder(src, 3, 4, 5, 6, cv2.BORDER_CONSTANT, value=(255, 128, 0)))
res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
save("match", res.astype(np.float32))
n, labels, stats, cents = cv2.connectedComponentsWithStats(bw)
boxes = [list(cv2.boundingRect(c)) for c in cv2.findContours(cv2.Canny(bw, 50, 150), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]]
hist = cv2.calcHist([src[..., 2]], [0], None, [5], [0, 256]).ravel().tolist()
json.dump({"otsu": float(t), "n": int(n), "stats": stats[1:].tolist(), "boxes": boxes, "hist": hist, "match_shape": list(res.shape),
           "cv2": cv2.__version__}, open(f"{out}/meta.json", "w"))
save("labels", labels.astype(np.int32))
`

func python(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("HIRO_PARITY_PYTHON"); p != "" {
		return p
	}
	p, err := pybridge.Python()
	if err != nil {
		t.Skip("没有 Python（设 HIRO_PARITY_PYTHON）")
	}
	return p
}

func TestOpsMatchCV2(t *testing.T) {
	dir := t.TempDir()
	if out, err := exec.Command(python(t), "-c", opsScript, dir).CombinedOutput(); err != nil {
		t.Skipf("Python 没有 cv2：%v %s", err, out)
	}
	read := func(name string) []byte {
		b, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	var meta struct {
		Otsu       float32   `json:"otsu"`
		N          int       `json:"n"`
		Stats      [][]int   `json:"stats"`
		Boxes      [][]int   `json:"boxes"`
		Hist       []float32 `json:"hist"`
		MatchShape []int     `json:"match_shape"`
		CV2        string    `json:"cv2"`
	}
	if err := json.Unmarshal(read("meta.json"), &meta); err != nil {
		t.Fatal(err)
	}
	t.Logf("Python cv2 %s", meta.CV2)
	src := &img.BGR{W: 160, H: 200, Pix: read("src")}
	gray := Gray(src)
	same := func(name string, got []byte) {
		t.Helper()
		want := read(name)
		if !bytes.Equal(got, want) {
			diff := 0
			for i := range min(len(got), len(want)) {
				if got[i] != want[i] {
					diff++
				}
			}
			t.Errorf("%s：%d / %d 字节不一样（长度 %d / %d）", name, diff, len(want), len(got), len(want))
		}
	}
	same("gray", gray.Pix)
	same("hsv", HSV(src).Pix)
	same("cubic", ResizeScale(src, 3, Cubic).Pix)
	same("area", Resize(src, 80, 100, Area).Pix)
	same("inrange", InRange(HSV(src), [3]float64{10, 50, 50}, [3]float64{25, 255, 255}).Pix)
	same("thresh", Threshold(gray, 127, 255).Pix)
	same("thresh_bgr", ThresholdBGR(src, 127, 255).Pix)
	ot, otsu := Otsu(gray)
	same("otsu", otsu.Pix)
	if ot != meta.Otsu {
		t.Errorf("otsu threshold %v vs %v", ot, meta.Otsu)
	}
	bw := Threshold(gray, 200, 255)
	k := RectKernel{7, 7}
	same("dilate", Dilate(bw, k, 2).Pix)
	same("erode", Erode(bw, k, 1).Pix)
	same("close", Morphology(bw, MorphClose, k).Pix)
	same("canny", Canny(ThresholdBGR(src, 190, 255), 200, 250).Pix)
	same("border", BorderConstant(src, 3, 4, 5, 6, [3]float64{255, 128, 0}).Pix)
	tpl := gray.Crop(40, 50, 50, 30)
	sc := MatchTemplate(gray, tpl, CcoeffNormed)
	if sc.W != meta.MatchShape[1] || sc.H != meta.MatchShape[0] {
		t.Fatalf("match shape %dx%d vs %v", sc.W, sc.H, meta.MatchShape)
	}
	want := read("match")
	maxd := 0.0
	for i, v := range sc.Val {
		w := math.Float32frombits(binary.LittleEndian.Uint32(want[i*4:]))
		maxd = math.Max(maxd, math.Abs(float64(v-w)))
	}
	if maxd > 1e-4 {
		t.Errorf("matchTemplate 分数最大差 %g", maxd)
	}
	labels, comps := Components(bw)
	if len(comps) != meta.N-1 {
		t.Fatalf("components %d vs %d", len(comps), meta.N-1)
	}
	for i, c := range comps {
		s := meta.Stats[i]
		if c.X != s[0] || c.Y != s[1] || c.W != s[2] || c.H != s[3] || c.Area != s[4] {
			t.Errorf("component %d: %+v vs %v", i, c, s)
		}
	}
	wantLab := read("labels")
	for i, l := range labels {
		if int32(binary.LittleEndian.Uint32(wantLab[i*4:])) != l { //nolint:gosec // 标签号
			t.Fatalf("label %d differs", i)
		}
	}
	boxes := ExternalBoxes(CannyGray(bw, 50, 150))
	if len(boxes) != len(meta.Boxes) {
		t.Fatalf("contours %d vs %d", len(boxes), len(meta.Boxes))
	}
	for i, b := range boxes {
		w := meta.Boxes[i]
		if b.X != w[0] || b.Y != w[1] || b.W != w[2] || b.H != w[3] {
			t.Errorf("contour %d: %+v vs %v", i, b, w)
		}
	}
	h := Hist(Channel(src, 2), 5, 0, 256)
	for i := range h {
		if h[i] != meta.Hist[i] {
			t.Errorf("hist %v vs %v", h, meta.Hist)
			break
		}
	}
	png, err := EncodePNG(src)
	if err != nil {
		t.Fatal(err)
	}
	back, err := Decode(png)
	if err != nil || !bytes.Equal(back.Pix, src.Pix) {
		t.Fatal("PNG round trip")
	}
}
