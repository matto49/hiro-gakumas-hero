//go:build darwin && cgo

package ocr

/*
#cgo CFLAGS: -x objective-c -fobjc-arc
#cgo LDFLAGS: -framework Foundation -framework Vision
#include <stdlib.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

typedef struct {
	char  *text;
	float  conf;
	double x, y, w, h; // 归一化坐标，原点在左下（Vision 的 boundingBox）
} hiro_ocr_item;

// 和 Python 版 _vision_raw 一样的请求：accurate、指定语言、不做语言纠正。返回条数；出错返回 -1。
static int hiro_vision_ocr(const void *png, int len, const char **langs, int nlangs, hiro_ocr_item **out) {
	@autoreleasepool {
		NSData *data = [NSData dataWithBytes:png length:(NSUInteger)len];
		VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithData:data options:@{}];
		VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
		req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
		NSMutableArray *ls = [NSMutableArray arrayWithCapacity:(NSUInteger)nlangs];
		for (int i = 0; i < nlangs; i++) [ls addObject:[NSString stringWithUTF8String:langs[i]]];
		req.recognitionLanguages = ls;
		req.usesLanguageCorrection = NO;
		NSError *err = nil;
		if (![handler performRequests:@[req] error:&err]) return -1;
		NSArray *results = req.results;
		int n = (int)results.count;
		*out = n ? (hiro_ocr_item *)calloc((size_t)n, sizeof(hiro_ocr_item)) : NULL;
		int k = 0;
		for (VNRecognizedTextObservation *obs in results) {
			VNRecognizedText *cand = [[obs topCandidates:1] firstObject];
			if (cand == nil) continue;
			CGRect bb = obs.boundingBox;
			(*out)[k].text = strdup(cand.string.UTF8String ?: "");
			(*out)[k].conf = cand.confidence;
			(*out)[k].x = bb.origin.x; (*out)[k].y = bb.origin.y; (*out)[k].w = bb.size.width; (*out)[k].h = bb.size.height;
			k++;
		}
		return k;
	}
}

static void hiro_ocr_free(hiro_ocr_item *items, int n) {
	for (int i = 0; i < n; i++) free(items[i].text);
	free(items);
}
*/
import "C"

import (
	"errors"
	"math"
	"unsafe"
)

// visionAvailable：Mac 上用 cgo 编的才有 Apple Vision。
const visionAvailable = true

// visionRaw 用 Apple Vision 识别一张 PNG；框按归一化坐标换回原图尺寸 (w, h)（同 Python _vision_raw）。
func visionRaw(png []byte, w, h int, languages []string) ([]Item, error) {
	if len(png) == 0 {
		return nil, nil
	}
	cl := make([]*C.char, len(languages))
	for i, l := range languages {
		cl[i] = C.CString(l)
	}
	defer func() {
		for _, c := range cl {
			C.free(unsafe.Pointer(c))
		}
	}()
	var langs **C.char
	if len(cl) > 0 {
		langs = &cl[0]
	}
	var items *C.hiro_ocr_item
	n := C.hiro_vision_ocr(unsafe.Pointer(&png[0]), C.int(len(png)), langs, C.int(len(cl)), &items) //nolint:gocritic // cgo 传切片首地址
	if n < 0 {
		return nil, errors.New("用 Apple Vision 识别失败")
	}
	defer C.hiro_ocr_free(items, n)
	out := make([]Item, 0, int(n))
	for _, it := range unsafe.Slice(items, int(n)) {
		x1 := float64(it.x) * float64(w)
		y1 := (1 - float64(it.y) - float64(it.h)) * float64(h)
		out = append(out, Item{
			Text: C.GoString(it.text), Conf: float64(it.conf),
			Box: Box{X1: round(x1), Y1: round(y1), X2: round(x1 + float64(it.w)*float64(w)), Y2: round(y1 + float64(it.h)*float64(h))},
		})
	}
	return out, nil
}

// round 同 Python 的 round()（五取偶）。
func round(v float64) int { return int(math.RoundToEven(v)) }
