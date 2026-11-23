package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
)

// fileKeys 把模型的 API Key 存在数据目录的 model-keys.json（权限 0600，只有本人能读）。
// 不用系统钥匙串：开发版每次重新构建签名都变，钥匙串每次都弹授权框（用户 11-23 定：Key 直接放配置里）。
// 和 settings.json 分开放：设置文件可以随便看 / 截图，不会带出 Key。
type fileKeys struct {
	Path string
}

var fileKeysMu sync.Mutex

func (k fileKeys) load() (map[string]string, error) {
	m := map[string]string{}
	b, err := os.ReadFile(k.Path)
	if errors.Is(err, os.ErrNotExist) {
		return m, nil
	}
	if err != nil {
		return nil, err
	}
	if len(b) == 0 {
		return m, nil
	}
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func (k fileKeys) save(m map[string]string) error {
	if err := os.MkdirAll(filepath.Dir(k.Path), 0o755); err != nil {
		return err
	}
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	tmp := k.Path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	if err := os.Chmod(tmp, 0o600); err != nil { // umask 宽的时候 WriteFile 的权限不一定生效
		return err
	}
	return os.Rename(tmp, k.Path)
}

func (k fileKeys) Get(name string) (string, error) {
	fileKeysMu.Lock()
	defer fileKeysMu.Unlock()
	m, err := k.load()
	if err != nil {
		return "", err
	}
	v, ok := m[name]
	if !ok || v == "" {
		return "", errNoKey
	}
	return v, nil
}

func (k fileKeys) Set(name, value string) error {
	fileKeysMu.Lock()
	defer fileKeysMu.Unlock()
	m, err := k.load()
	if err != nil {
		return err
	}
	m[name] = value
	return k.save(m)
}

func (k fileKeys) Delete(name string) error {
	fileKeysMu.Lock()
	defer fileKeysMu.Unlock()
	m, err := k.load()
	if err != nil {
		return err
	}
	delete(m, name)
	return k.save(m)
}
