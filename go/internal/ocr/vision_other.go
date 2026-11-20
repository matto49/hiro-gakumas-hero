//go:build !darwin || !cgo

package ocr

import "errors"

// visionAvailable：Apple Vision 只有 macOS（且用 cgo 编）才有。
const visionAvailable = false

func visionRaw([]byte, int, int, []string) ([]Item, error) {
	return nil, errors.New("Apple Vision 只有 macOS 上有")
}
