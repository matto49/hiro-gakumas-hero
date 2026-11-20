// Package onnx 是模型推理（onnxruntime，经 github.com/yalue/onnxruntime_go 运行时加载动态库）：ddddocr、RapidOCR、UINet 共用。
//
// 动态库位置：HIRO_ONNXRUNTIME → 程序旁边（安装包带的）→ Homebrew（/opt/homebrew/lib、/usr/local/lib）。
// 模型文件位置见 [Model]。
package onnx

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"

	ort "github.com/yalue/onnxruntime_go"

	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
)

// ErrUnavailable 是没有 onnxruntime 动态库或模型文件。
var ErrUnavailable = errors.New("onnxruntime 不可用")

func libName() string {
	switch runtime.GOOS {
	case "darwin":
		return "libonnxruntime.dylib"
	case "windows":
		return "onnxruntime.dll"
	}
	return "libonnxruntime.so"
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// Library onnxruntime 动态库的路径；找不到返回空。
func Library() string {
	if v := os.Getenv("HIRO_ONNXRUNTIME"); v != "" {
		return v
	}
	var cands []string
	if exe, err := os.Executable(); err == nil {
		cands = append(cands, filepath.Join(filepath.Dir(exe), libName()))
	}
	cands = append(cands, filepath.Join("/opt/homebrew/lib", libName()), filepath.Join("/usr/local/lib", libName()))
	for _, c := range cands {
		if exists(c) {
			return c
		}
	}
	return ""
}

var (
	initOnce sync.Once
	initErr  error
)

// Init 加载 onnxruntime（只做一次）。
func Init() error {
	initOnce.Do(func() {
		lib := Library()
		if lib == "" {
			initErr = fmt.Errorf("%w：找不到 %s（设 HIRO_ONNXRUNTIME，或 brew install onnxruntime）", ErrUnavailable, libName())
			return
		}
		ort.SetSharedLibraryPath(lib)
		if err := ort.InitializeEnvironment(); err != nil {
			initErr = fmt.Errorf("%w：%w", ErrUnavailable, err)
		}
	})
	return initErr
}

// Model 模型文件的路径（rel 如 "ddddocr/common_old.onnx"）：HIRO_MODELS → 仓库的 cache/models（开发机）→ HIRO_HOME/models（安装后）。
func Model(rel string) (string, error) {
	var roots []string
	if v := os.Getenv("HIRO_MODELS"); v != "" {
		roots = append(roots, v)
	}
	if wd, err := os.Getwd(); err == nil {
		for d := wd; ; d = filepath.Dir(d) {
			roots = append(roots, filepath.Join(d, "cache", "models"))
			if filepath.Dir(d) == d {
				break
			}
		}
	}
	if h := home.Dir(); h != "" {
		roots = append(roots, filepath.Join(h, "models"))
	}
	for _, r := range roots {
		if p := filepath.Join(r, rel); exists(p) {
			return p, nil
		}
	}
	return "", fmt.Errorf("%w：没有模型 %s（放到 cache/models/ 或设 HIRO_MODELS）", ErrUnavailable, rel)
}

// Session 是一个模型的推理会话（输入输出按名字，形状可变）。并发安全：内部加锁串行跑。
type Session struct {
	mu      sync.Mutex
	s       *ort.DynamicAdvancedSession
	Inputs  []string
	Outputs []string
}

// Open 打开模型。
func Open(path string) (*Session, error) {
	if err := Init(); err != nil {
		return nil, err
	}
	ins, outs, err := ort.GetInputOutputInfo(path)
	if err != nil {
		return nil, err
	}
	s := &Session{}
	for _, i := range ins {
		s.Inputs = append(s.Inputs, i.Name)
	}
	for _, o := range outs {
		s.Outputs = append(s.Outputs, o.Name)
	}
	s.s, err = ort.NewDynamicAdvancedSession(path, s.Inputs, s.Outputs, nil)
	if err != nil {
		return nil, err
	}
	return s, nil
}

// Output 是一个输出张量（float32）。
type Output struct {
	Shape []int64
	Data  []float32
}

// RunF32 用一个 float32 输入跑一次，返回全部 float32 输出。
func (s *Session) RunF32(shape []int64, data []float32) ([]Output, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	in, err := ort.NewTensor(ort.NewShape(shape...), data)
	if err != nil {
		return nil, err
	}
	defer func() { _ = in.Destroy() }()
	outs := make([]ort.Value, len(s.Outputs))
	if err := s.s.Run([]ort.Value{in}, outs); err != nil {
		return nil, err
	}
	res := make([]Output, len(outs))
	for i, v := range outs {
		t, ok := v.(*ort.Tensor[float32])
		if !ok {
			_ = v.Destroy()
			return nil, fmt.Errorf("输出 %s 不是 float32", s.Outputs[i])
		}
		res[i] = Output{Shape: t.GetShape(), Data: append([]float32(nil), t.GetData()...)}
		_ = t.Destroy()
	}
	return res, nil
}
