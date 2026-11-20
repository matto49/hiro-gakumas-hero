//go:build !windows

package device

import (
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// DMM 在 Windows 以外的系统上只是占位：都返回 Unavailable。
type DMM struct {
	opts   dmmOptions
	screen Screen
}

// NewDMM 建 DMM 设备（Windows 以外不可用）。
func NewDMM() *DMM { return &DMM{opts: dmmOptionsFromEnv()} }

func errNotWindows() error { return unavailable("DMM 版只在 Windows 上") }

func (d *DMM) Name() string        { return "maafw-win32" }
func (d *DMM) Orientation() string { return d.screen.Orientation() }

func (d *DMM) Connect() error                { return errNotWindows() }
func (d *DMM) Close() error                  { return nil }
func (d *DMM) Screenshot() (*img.BGR, error) { return nil, errNotWindows() }

func (d *DMM) Tap(int, int) error { return errNotWindows() }

func (d *DMM) Swipe(int, int, int, int, time.Duration, time.Duration) error { return errNotWindows() }
func (d *DMM) Alive() bool                                                  { return false }

// FitDMMWindow 只有 Windows 有。
func FitDMMWindow(size *[2]int) (before, after [2]int, err error) {
	return before, after, errNotWindows()
}

// DMMWindowInfo 只有 Windows 有。
func DMMWindowInfo() (string, error) { return "", errNotWindows() }
