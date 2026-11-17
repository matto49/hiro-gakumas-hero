package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLegacyDeepSeekNamesBecomeFlash(t *testing.T) {
	for _, old := range []string{"", "deepseek-chat", "deepseek-reasoner"} {
		a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
		_ = a.Save(Settings{Provider: "deepseek", Model: old}, "sk-x")
		if s := a.Settings(); s.Model != "deepseek-flash" {
			t.Fatalf("%q → %q", old, s.Model)
		}
	}
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	_ = a.Save(Settings{Provider: "deepseek", Model: "deepseek-v4-pro"}, "sk-x")
	if s := a.Settings(); s.Model != "deepseek-v4-pro" {
		t.Fatalf("kept %q", s.Model)
	}
}

func TestModelsListsWhatTheKeyCanUse(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/models" || r.Header.Get("Authorization") != "Bearer sk-x" {
			w.WriteHeader(401)
			return
		}
		_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"deepseek-flash"},{"id":"deepseek-v4-pro"}]}`)
	}))
	defer srv.Close()
	a := &Assistant{Home: t.TempDir(), Keys: memKeys{}, Hiro: &Hiro{}}
	if _, err := a.Models(context.Background()); err == nil || !strings.Contains(err.Error(), "Key") {
		t.Fatalf("no key: %v", err)
	}
	_ = a.Save(Settings{Provider: "deepseek", BaseURL: srv.URL}, "sk-x")
	got, err := a.Models(context.Background())
	if err != nil || strings.Join(got, ",") != "deepseek-flash,deepseek-v4-pro" {
		t.Fatalf("models %v err %v", got, err)
	}
}
