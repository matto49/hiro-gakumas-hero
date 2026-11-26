package main

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// G1 §8 错误文案：模型服务的各种出错都给白话原因和下一步按钮，不把 Go 的原始报错给玩家。
func TestModelErrorsArePlain(t *testing.T) {
	cases := []struct {
		name    string
		status  int
		body    string
		msg     string
		actions []string
	}{
		{"key", 401, "", "API Key", []string{plugin.ActionSettings}},
		{"balance", 402, "", "余额不足", []string{plugin.ActionRetry, plugin.ActionSettings}},
		{"server", 503, "busy", "模型服务出错了（503）", []string{plugin.ActionRetry}},
		{"garbage", 200, "not json", "回答看不懂", []string{plugin.ActionRetry, plugin.ActionSettings}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(c.status)
				_, _ = io.WriteString(w, c.body)
			}))
			defer srv.Close()
			_, err := OpenAICompat{BaseURL: srv.URL, Model: "m", Key: "k", Client: http.DefaultClient}.Chat(context.Background(), nil, nil)
			var ue *plugin.UserError
			if !errors.As(err, &ue) || !strings.Contains(ue.Message, c.msg) || ue.Hint == "" || strings.Join(ue.Actions, ",") != strings.Join(c.actions, ",") {
				t.Fatalf("得到 %#v", err)
			}
		})
	}
}

func TestModelOfflineIsPlain(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close() // 连不上
	_, err := OpenAICompat{BaseURL: url, Model: "m", Key: "k", Client: http.DefaultClient}.Chat(context.Background(), nil, nil)
	var ue *plugin.UserError
	if !errors.As(err, &ue) || !strings.Contains(ue.Message, "连不上模型服务") || strings.Contains(ue.Message, "dial tcp") || ue.Err == nil {
		t.Fatalf("得到 %#v", err)
	}
	if len(ue.Actions) != 1 || ue.Actions[0] != plugin.ActionRetry {
		t.Fatalf("按钮 %v", ue.Actions)
	}
	// 玩家取消（关窗口 / 退出）不算网络问题：保留原始的取消错误，不包成「连不上模型服务」（#129 review）
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = OpenAICompat{BaseURL: url, Model: "m", Key: "k", Client: http.DefaultClient}.Chat(ctx, nil, nil)
	var ce *plugin.UserError
	if !errors.Is(err, context.Canceled) || errors.As(err, &ce) {
		t.Fatalf("取消时 %#v", err)
	}
}

// 响应头到了、正文读到一半玩家取消：调用方要能认出 context.Canceled（#129 review）。
func TestModelCancelWhileReadingBody(t *testing.T) {
	headers := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		_, _ = io.WriteString(w, `{"choices":[`)
		w.(http.Flusher).Flush()
		close(headers)
		<-r.Context().Done() // 正文一直不发完，直到请求被取消
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	go func() { <-headers; cancel() }()
	_, err := OpenAICompat{BaseURL: srv.URL, Model: "m", Key: "k", Client: http.DefaultClient}.Chat(ctx, nil, nil)
	var ue *plugin.UserError
	if !errors.Is(err, context.Canceled) || errors.As(err, &ue) {
		t.Fatalf("读正文时取消 %#v", err)
	}
}
