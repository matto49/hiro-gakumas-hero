package main

import (
	"context"
	"net/http"
	"path"
	"path/filepath"
	"regexp"
	"strings"
)

// homeFiles 把 HIRO_HOME 里界面要用的图交给网页（hiro://localhost/…）：
//
//	/sprites/<asset>.png            资料库小图（catalog/sprites 里的 <asset>.png 或 .webp：攻略站上支援卡卡面、偶像卡是 webp；
//	                                没有就让工具层下载）
//	/traces/<任务>/frames/<n>.jpg    对局截图（work/traces）
//	/review/<任务>.html              复盘页（hiro run review 生成在 jobs/<任务>/review.html）
//
// 只认这两种路径；信息库、service.json（带令牌）等其他文件一律 404。
func homeFiles(home string, fetchSprite func(ctx context.Context, asset string) error) http.Handler {
	sprite := regexp.MustCompile(`^/sprites/([A-Za-z0-9_.\-]+)\.png$`)
	frame := regexp.MustCompile(`^/traces/([A-Za-z0-9_\-]+)/frames/([0-9]+\.jpg)$`)
	review := regexp.MustCompile(`^/review/([A-Za-z0-9_\-]+)\.html$`)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		if p != path.Clean(p) || strings.Contains(p, "..") {
			http.NotFound(w, r)
			return
		}
		if m := sprite.FindStringSubmatch(p); m != nil {
			find := func() string {
				for _, ext := range []string{".png", ".webp"} {
					if f := filepath.Join(home, "catalog", "sprites", m[1]+ext); exists(f) {
						return f
					}
				}
				return ""
			}
			file := find()
			if file == "" && fetchSprite != nil {
				_ = fetchSprite(r.Context(), m[1])
				file = find()
			}
			if file != "" {
				w.Header().Set("Cache-Control", "max-age=604800")
				http.ServeFile(w, r, file)
				return
			}
		}
		if m := frame.FindStringSubmatch(p); m != nil {
			file := filepath.Join(home, "work", "traces", m[1], "frames", m[2])
			if exists(file) {
				http.ServeFile(w, r, file)
				return
			}
		}
		if m := review.FindStringSubmatch(p); m != nil {
			file := filepath.Join(home, "jobs", m[1], "review.html")
			if exists(file) {
				http.ServeFile(w, r, file)
				return
			}
		}
		http.NotFound(w, r)
	})
}
