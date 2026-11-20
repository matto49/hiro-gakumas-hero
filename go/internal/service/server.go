package service

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
)

// ServiceCommand 是 hiro 的隐藏子命令：起后台服务（客户端发现服务没在跑时自己拉起）。
const ServiceCommand = "__service"

// envelope 同 cli.Envelope（服务不依赖 cli 包）。
func okEnv(data any) *jsonx.Object { return jsonx.NewObject().Set("ok", true).Set("data", data) }

func errEnv(err error) *jsonx.Object {
	e := clierr.As(err)
	return jsonx.NewObject().Set("ok", false).Set("error", jsonx.NewObject().Set("code", e.Code).Set("message", e.Message).Set("hint", e.Hint))
}

// Handler 是服务的 HTTP 接口：都是 POST + JSON，请求头 X-Hiro-Token 要对得上。
// /ping /status /start /events /pause /takeover /resume /stop /shutdown（同 Python 版）。
func Handler(m *Manager, token string, shutdown func()) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		send := func(code int, v any) {
			data, _ := jsonx.Marshal(v)
			w.Header().Set("Content-Type", "application/json; charset=utf-8")
			w.Header().Set("X-Content-Type-Options", "nosniff")
			w.WriteHeader(code)
			_, _ = w.Write(data) //nolint:gosec // JSON 响应（nosniff），只回给带令牌的本机客户端
		}
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("X-Hiro-Token")), []byte(token)) != 1 {
			send(http.StatusUnauthorized, errEnv(clierr.New("unauthorized", "令牌不对", "")))
			return
		}
		if r.Method != http.MethodPost {
			send(http.StatusMethodNotAllowed, errEnv(clierr.New("bad_request", "只收 POST", "")))
			return
		}
		raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		body := jsonx.NewObject()
		if err == nil && len(strings.TrimSpace(string(raw))) > 0 {
			body, err = jsonx.DecodeObject(raw)
		}
		if err != nil {
			send(http.StatusBadRequest, errEnv(clierr.New("bad_request", "请求不是 JSON", "")))
			return
		}
		reply := func(data any, err error) {
			if err != nil {
				send(http.StatusOK, errEnv(err))
				return
			}
			send(http.StatusOK, okEnv(data))
		}
		switch path := strings.Trim(r.URL.Path, "/"); path {
		case "ping":
			reply(jsonx.NewObject().Set("pid", os.Getpid()), nil)
		case "status":
			reply(m.Status(), nil)
		case "start":
			llm := map[string]string{}
			le := jsonx.Obj(body, "llm_env")
			for _, k := range le.Keys() {
				llm[k] = jsonx.Str(le, k)
			}
			reply(m.Start(jsonx.Str(body, "kind"), jsonx.Obj(body, "params"), llm))
		case "events":
			since, _ := jsonx.Int(body, "since")
			reply(m.Events(jsonx.Str(body, "job"), since))
		case "pause", "takeover", "resume", "stop":
			reply(m.Control(path, jsonx.Str(body, "job")))
		case "shutdown":
			m.StopCurrent()
			reply(jsonx.NewObject(), nil)
			go shutdown()
		default:
			send(http.StatusNotFound, errEnv(clierr.New("not_found", "没有这个接口："+path, "")))
		}
	})
}

// Info 是 HIRO_HOME/service.json：{port, token, pid}。
type Info struct {
	Port  int    `json:"port"`
	Token string `json:"token"`
	PID   int    `json:"pid"`
}

func writeInfo(h string, info Info) (string, error) {
	p := filepath.Join(h, "service.json")
	tmp := p + ".tmp"
	data, err := jsonx.Marshal(info)
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return "", err
	}
	return p, os.Rename(tmp, p)
}

// Serve 跑服务直到 /shutdown。整个服务进程一直拿着 service.lock：同一个 HIRO_HOME 只能有一个服务。返回退出码。
func Serve() int {
	h, err := home.Ensure()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	lock := NewFileLock(filepath.Join(h, "service.lock"))
	if ok, err := lock.Acquire(false, 0); !ok || err != nil {
		fmt.Fprintln(os.Stderr, "已经有一个 hiro 服务在跑")
		return 3
	}
	defer lock.Release()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	token := hex.EncodeToString(b)
	m := NewManager(h)
	srv := &http.Server{ReadHeaderTimeout: 10 * time.Second}
	srv.Handler = Handler(m, token, func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(ctx)
	})
	port := ln.Addr().(*net.TCPAddr).Port
	infoPath, err := writeInfo(h, Info{Port: port, Token: token, PID: os.Getpid()})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer func() { // 还是自己写的那份才删（别的服务可能已经接手）
		if cur, err := readInfo(infoPath); err == nil && cur.PID == os.Getpid() {
			_ = os.Remove(infoPath)
		}
	}()
	if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	return 0
}
