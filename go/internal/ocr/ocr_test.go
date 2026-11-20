package ocr

import (
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/pybridge"
)

func TestSanitize(t *testing.T) {
	if got := Sanitize("ＡＢＣ１２３（あ）ó"); got != "ABC123(あ)6" {
		t.Fatalf("%q", got)
	}
}

// 同一批截图区域，Python（hiro.vision.ocr.ocr_boxes）和 Go 都用 Apple Vision 读：文字、框要一样，置信度差 0.01 以内。
const visionScript = `
import sys, json, cv2
sys.path.insert(0, sys.argv[1])
from hiro.vision.ocr import ocr_boxes
img = cv2.imread(sys.argv[2])
out = []
for (x, y, w, h, s) in json.loads(sys.argv[3]):
    out.append([[t, c, list(b)] for t, c, b in ocr_boxes(img[y:y+h, x:x+w], scale=s)])
print(json.dumps(out, ensure_ascii=False))
`

func TestVisionMatchesPython(t *testing.T) {
	if !visionAvailable {
		t.Skip("没有 Apple Vision")
	}
	py := os.Getenv("HIRO_PARITY_PYTHON")
	if py == "" {
		t.Skip("设 HIRO_PARITY_PYTHON 为装了 pyobjc Vision 的 Python")
	}
	shot := os.Getenv("HIRO_OCR_SAMPLE")
	if shot == "" {
		shot = filepath.Join(os.Getenv("HOME"), "github", "digital-hiro", "tests", "fixtures", "action_select")
		matches, _ := filepath.Glob(filepath.Join(shot, "*.png"))
		jpgs, _ := filepath.Glob(filepath.Join(shot, "*.jpg"))
		matches = append(matches, jpgs...)
		if len(matches) == 0 {
			t.Skip("没有样例截图（设 HIRO_OCR_SAMPLE）")
		}
		shot = matches[0]
	}
	repo, err := pybridge.Repo()
	if err != nil {
		t.Skip(err)
	}
	regions := [][5]float64{{0, 0, 720, 300, 1.5}, {0, 300, 720, 400, 3}, {100, 900, 500, 200, 3}, {0, 0, 720, 1280, 1.5}}
	arg, _ := json.Marshal(regions)
	t.Setenv("HIRO_OCR", "vision")
	cmd := exec.Command(py, "-c", visionScript, filepath.Join(repo, "src"), shot, string(arg)) //nolint:gosec // 测试：跑本机配置的 Python
	cmd.Env = append(os.Environ(), "HIRO_OCR=vision")
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("Python 跑不了：%v", err)
	}
	var want [][][3]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("%v %s", err, out)
	}
	src, err := cv.Read(shot)
	if err != nil {
		t.Fatal(err)
	}
	for i, r := range regions {
		x, y, w, h := int(r[0]), int(r[1]), int(r[2]), int(r[3])
		got, err := Boxes(src.Slice(x, y, x+w, y+h), r[4], JaEn)
		if err != nil {
			t.Fatal(err)
		}
		if len(got) != len(want[i]) {
			t.Errorf("区域 %d：%d 段 vs Python %d 段", i, len(got), len(want[i]))
			continue
		}
		for j, g := range got {
			wt, wc, wb := want[i][j][0].(string), want[i][j][1].(float64), want[i][j][2].([]any)
			box := [4]int{int(wb[0].(float64)), int(wb[1].(float64)), int(wb[2].(float64)), int(wb[3].(float64))}
			if g.Text != wt || [4]int{g.Box.X1, g.Box.Y1, g.Box.X2, g.Box.Y2} != box || math.Abs(g.Conf-wc) > 0.01 {
				t.Errorf("区域 %d 第 %d 段：go %q %v %.3f / python %q %v %.3f", i, j, g.Text, g.Box, g.Conf, wt, box, wc)
			}
		}
	}
	t.Logf("样例 %s", shot)
}
