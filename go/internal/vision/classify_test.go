package vision

import (
	"testing"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/golden"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/vision/ui"
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

func TestCardMatches(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "probes:card_matches", Sample: 400, Allow: 8}, func(b *img.BGR) (any, error) {
		return safe(func() any {
			b = ui.Normalize(b)
			ms := []any{orNil(CardMatch(b.Slice(262, 556, 455, 750), cardMinVotes))}
			for _, r := range CardLetters(b) {
				if c := cardCrop(b, r); c != nil {
					ms = append(ms, orNil(CardMatch(c, cardMinVotes)))
				} else {
					ms = append(ms, nil)
				}
			}
			return map[string]any{"matches": ms, "recommended": RecommendedFlags(b, SelectCardOptions(b))}
		})
	})
}

func digitBoxes() []ocr.Box {
	return []ocr.Box{
		LayoutBox(examView, "ExamAuditionTurnView/txt/TurnValue"), boxGenki(), LayoutBox(examView, "GaugeRoot/Text/StaminaValue"),
		{X1: 222, Y1: 128, X2: 262, Y2: 166},
		{X1: 683, Y1: 228, X2: 715, Y2: 256},
		{X1: 322, Y1: 50, X2: 436, Y2: 94},
		{X1: 330, Y1: 94, X2: 440, Y2: 136},
		{X1: 500, Y1: 120, X2: 700, Y2: 170},
	}
}

func TestDigitReads(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "probes:digit_reads", Sample: 400, Allow: 8}, func(b *img.BGR) (any, error) {
		return safe(func() any {
			out := []any{}
			for _, bx := range digitBoxes() {
				if b.H < bx.Y2 || b.W < bx.X2 {
					out = append(out, nil)
					continue
				}
				t1, u1 := ReadText(b, bx, false)
				t2, u2 := ReadText(b, bx, true)
				var iv any
				if v, ok := ReadInt(b, bx, false); ok {
					iv = v
				}
				var lead [2]any
				if v, ok, suf := ReadLeadingInt(b, bx); ok {
					lead = [2]any{v, orNil(suf)}
				} else {
					lead = [2]any{nil, orNil(suf)}
				}
				var sv any
				v, ok, m, err := ReadScore(b, bx)
				if err != nil {
					panic(err)
				}
				if ok {
					sv = v
				}
				out = append(out, map[string]any{"text": []any{t1, u1}, "aligned": []any{t2, u2}, "int": iv, "lead": lead, "score": sv, "method": m.Method})
			}
			return out
		})
	})
}

func TestActionSelect(t *testing.T) {
	golden.Compare(t, golden.Spec{Call: "probes:action_select", Pages: []string{"ACTION_SELECT"}, Allow: 2}, func(b *img.BGR) (any, error) {
		var out any
		err := func() (err error) {
			defer cv.Recover(&err)
			st, err := ReadActionSelect(b)
			out = st
			return err
		}()
		return out, err
	})
}

func TestDeckPages(t *testing.T) {
	pages := []string{"DECK_LIST", "SKILL_CARD_REMOVAL", "SKILL_CARD_ENHANCE", "SKILL_CARD_CHANGE_1", "SKILL_CARD_CHANGE_2", "DIALOG", "CONSULT"}
	golden.Compare(t, golden.Spec{Call: "probes:deck_pages", Pages: pages, Allow: 4}, func(b *img.BGR) (any, error) {
		var out any
		err := func() (err error) {
			defer cv.Recover(&err)
			var dl any
			if IsDeckList(b) {
				d, err := ReadDeckList(b)
				if err != nil {
					return err
				}
				dl = d
			}
			dia, err := ReadCardDialog(b, removalGrid, detailBox, "")
			if err != nil {
				return err
			}
			ch, err := ReadChangeCandidates(b)
			if err != nil {
				return err
			}
			cf, err := ReadConfirmDialog(b)
			if err != nil {
				return err
			}
			out = map[string]any{"deck_list": dl, "dialog": dia, "change1": ch, "confirm": cf, "scroll": DialogScroll(b, removalGrid, 683)}
			return nil
		}()
		return out, err
	})
}
