package vision

import (
	"os"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/golden"
)

// 从零建卡面图库索引要多久（HIRO_CARDDB_BUILD=1 时跑；配合空的 HIRO_CACHE）。
func TestCardDBBuildTime(t *testing.T) {
	if os.Getenv("HIRO_CARDDB_BUILD") == "" {
		t.Skip("设 HIRO_CARDDB_BUILD=1")
	}
	golden.Cases(t)
	t0 := time.Now()
	db := loadCardDB()
	t.Logf("build %v vecs %d centroids %d", time.Since(t0), len(db.Owner), len(db.Centroids)/cardDim)
}
