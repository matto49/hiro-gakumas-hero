package vision

import (
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/golden"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// safe 跑 fn，OpenCV 报错转成 error（Python 那边是 cv2.error 异常）。
func safe(fn func() any) (out any, err error) {
	defer cv.Recover(&err)
	return fn(), nil
}

func orNil(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func TestTemplateScene(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.classify:template_scene"}, func(b *img.BGR) (any, error) {
		return safe(func() any { return orNil(TemplateScene(b)) })
	})
}

func TestOptionRegions(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.classify:option_regions"}, func(b *img.BGR) (any, error) {
		return safe(func() any { return OptionRegions(b) })
	})
}

func TestAtCommu(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.classify:at_commu"}, func(b *img.BGR) (any, error) {
		return safe(func() any { return AtCommu(b) })
	})
}

func TestSignatureMatch(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.signature:match", Tol: 2e-3, Allow: 2}, func(b *img.BGR) (any, error) {
		return safe(func() any {
			name, detail := SignatureMatch(b)
			return []any{orNil(name), detail}
		})
	})
}

func TestQuickPage(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.quick:quick_page", Ignore: []string{"ms"}}, func(b *img.BGR) (any, error) {
		q, err := QuickPage(b)
		if q == nil || err != nil {
			return nil, err
		}
		return q, nil
	})
}

func TestFallbackPage(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "hiro.vision.quick:fallback_page"}, func(b *img.BGR) (any, error) {
		return safe(func() any { return orNil(FallbackPage(b)) })
	})
}
