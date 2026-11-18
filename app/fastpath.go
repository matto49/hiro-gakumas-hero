package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

// 界面每 1–2 秒查一次任务状态和事件。走命令行要起一个 Python 进程（几十 MB），一直开着程序就一直在起，
// 会给内存本来就紧的机器加压（2026-11-18 两次崩溃都是 WebKit 要不到内存）。这两条只读命令改成直接问 hiro 服务：
// 读 HIRO_HOME/service.json 的端口和令牌，POST 本机端口。服务没在跑、连不上时退回命令行（命令行会拉起服务）。

var fastClient = &http.Client{Timeout: 5 * time.Second}

type serviceInfo struct {
	Port  int    `json:"port"`
	Token string `json:"token"`
}

// fastPath：能直接问服务的命令 → 接口路径和请求体；不能的返回 ok=false。
func fastPath(args []string) (string, map[string]any, bool) {
	if len(args) < 2 || args[0] != "run" {
		return "", nil, false
	}
	switch args[1] {
	case "status":
		return "status", map[string]any{}, len(args) == 2
	case "events":
		body := map[string]any{}
		for i := 2; i < len(args); i++ {
			switch args[i] {
			case "--job":
				if i+1 >= len(args) {
					return "", nil, false
				}
				body["job"] = args[i+1]
				i++
			case "--since":
				if i+1 >= len(args) {
					return "", nil, false
				}
				n, err := strconv.Atoi(args[i+1])
				if err != nil {
					return "", nil, false
				}
				body["since"] = n
				i++
			default: // --follow 等：走命令行
				return "", nil, false
			}
		}
		return "events", body, true
	}
	return "", nil, false
}

func (h *Hiro) tryService(ctx context.Context, args []string) (Result, bool) {
	path, body, ok := fastPath(args)
	if !ok {
		return Result{}, false
	}
	raw, err := os.ReadFile(filepath.Join(HiroHome(), "service.json"))
	if err != nil {
		return Result{}, false
	}
	var info serviceInfo
	if json.Unmarshal(raw, &info) != nil || info.Port == 0 || info.Token == "" {
		return Result{}, false
	}
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", fmt.Sprintf("http://127.0.0.1:%d/%s", info.Port, path), bytes.NewReader(b))
	if err != nil {
		return Result{}, false
	}
	req.Header.Set("X-Hiro-Token", info.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := fastClient.Do(req)
	if err != nil {
		return Result{}, false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Result{}, false
	}
	var r Result
	if json.NewDecoder(resp.Body).Decode(&r) != nil {
		return Result{}, false
	}
	if !r.OK {
		r.Exit = 1
	}
	return r, true
}
