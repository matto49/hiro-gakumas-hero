package vision

import (
	"crypto/sha1" //nolint:gosec // 只做缓存签名
	"encoding/gob"
	"encoding/hex"
	"fmt"
	"hash/fnv"
	"log/slog"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// 卡面图库（同 Python hiro.vision.card_db）：选卡弹窗、相談货架、牌组卡格认卡。
//
// 图：KAA 公开数据 skill_cards/（IMREAD_COLOR）。描述子：OpenCV SIFT，nfeatures=500。查询：截图每个描述子找 1 个最近邻，
// 距离（L2 平方）≤ cardGoodDist 的给那张图投一票；按「票数多、其次最小距离小」排序。match：角色专属图（-hmsz 等后缀）合并计票，
// 最高 < cardMinVotes 票不认。
//
// 检索：Python 用 faiss IVFFlat（nlist 1024、nprobe 16，训练抽样靠 numpy 随机数）；这里同样是 IVF（nlist / nprobe 一样），
// 聚类中心用 OpenCV kmeans 训练，检索在 Go 里多协程做。近似检索两边选中的最近邻偶尔不同，对照比「认出哪张卡」。
// 描述子和索引第一次用时现算（约两千张，几十秒），存到缓存目录 card_sift_go.gob，图库文件变了就重算。

const (
	cardNFeatures   = 500
	cardGoodDist    = 20000.0
	cardMinVotes    = 3
	cardNList       = 1024
	cardNProbe      = 16
	cardIVFMin      = cardNList * 39
	cardTrainSample = cardNList * 100
	cardKMeansIter  = 25
	cardMemoSize    = 512
	cardDim         = 128
	cardCacheFormat = 1
)

// CardHit 是一张图的票数和最近一次匹配距离。
type CardHit struct {
	Key      string  `json:"key"`
	Votes    int     `json:"votes"`
	Distance float64 `json:"distance"`
}

type cardIndex struct {
	Format    int
	Sig       string
	Keys      []string
	Owner     []int32   // 每个描述子属于第几张图
	Vecs      []float32 // n × 128
	Centroids []float32 // nlist × 128；空 = 精确检索
	Lists     [][]int32 // 每个簇里的描述子下标
}

var (
	cardOnce sync.Once
	cardDB   *cardIndex
	cardMemo = struct {
		sync.Mutex
		order []uint64
		m     map[uint64][]CardHit
	}{m: map[uint64][]CardHit{}}
)

func cardSignature(dir string, files []string) string {
	h := sha1.New() //nolint:gosec // 缓存签名
	for _, f := range files {
		st, err := os.Stat(filepath.Join(dir, f))
		if err != nil {
			continue
		}
		fmt.Fprintf(h, "%s:%d:%d", f, st.Size(), st.ModTime().Unix())
	}
	return hex.EncodeToString(h.Sum(nil))
}

func cardFiles(dir string) []string {
	es, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range es {
		if strings.HasSuffix(e.Name(), ".png") {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

func loadCardDB() *cardIndex {
	cardOnce.Do(func() {
		dir := assets.Default().GameData("skill_cards")
		if dir == "" {
			cardDB = &cardIndex{}
			return
		}
		files := cardFiles(dir)
		sig := cardSignature(dir, files)
		path := filepath.Join(assets.CacheDir(), "card_sift_go.gob")
		if db := readCardCache(path); db != nil && db.Sig == sig && db.Format == cardCacheFormat {
			cardDB = db
			return
		}
		slog.Info("卡面图库：给卡图算 SIFT 描述子、建索引（只算一次）", "张数", len(files), "缓存", path)
		db := buildCardDB(dir, files)
		db.Sig, db.Format = sig, cardCacheFormat
		writeCardCache(path, db)
		cardDB = db
	})
	return cardDB
}

func readCardCache(path string) *cardIndex {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer func() { _ = f.Close() }()
	var db cardIndex
	if gob.NewDecoder(f).Decode(&db) != nil {
		return nil
	}
	return &db
}

func writeCardCache(path string, db *cardIndex) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return
	}
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return
	}
	if err := gob.NewEncoder(f).Encode(db); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return
	}
	_ = f.Close()
	_ = os.Rename(tmp, path)
}

func buildCardDB(dir string, files []string) *cardIndex {
	type res struct {
		key string
		d   *cv.F32
	}
	out := make([]res, len(files))
	var wg sync.WaitGroup
	sem := make(chan struct{}, runtime.NumCPU())
	for i, f := range files {
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer func() { <-sem; wg.Done() }()
			b, err := cv.Read(filepath.Join(dir, f))
			if err != nil {
				return
			}
			out[i] = res{strings.TrimSuffix(f, ".png"), cv.SIFT(b, cardNFeatures)}
		}()
	}
	wg.Wait()
	db := &cardIndex{}
	for _, r := range out {
		if r.d == nil || r.d.H == 0 {
			continue
		}
		k := int32(len(db.Keys)) //nolint:gosec // 两千多张
		db.Keys = append(db.Keys, r.key)
		db.Vecs = append(db.Vecs, r.d.Val...)
		for range r.d.H {
			db.Owner = append(db.Owner, k)
		}
	}
	n := len(db.Owner)
	if n < cardIVFMin {
		return db
	}
	step := max(1, n/cardTrainSample) // 均匀抽样训练（固定，每次建出来的簇一样）
	sample := &cv.F32{W: cardDim}
	for i := 0; i < n && sample.H < cardTrainSample; i += step {
		sample.Val = append(sample.Val, db.Vecs[i*cardDim:(i+1)*cardDim]...)
		sample.H++
	}
	cent := cv.KMeans(sample, cardNList, cardKMeansIter)
	db.Centroids = cent.Val
	db.Lists = make([][]int32, cardNList)
	assign := make([]int32, n)
	parallelFor(n, func(i int) {
		assign[i] = int32(nearestCentroid(db.Centroids, db.Vecs[i*cardDim:(i+1)*cardDim])) //nolint:gosec // < nlist
	})
	for i, c := range assign {
		db.Lists[c] = append(db.Lists[c], int32(i))
	}
	return db
}

func parallelFor(n int, fn func(i int)) {
	workers := runtime.NumCPU()
	var wg sync.WaitGroup
	for w := range workers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := w; i < n; i += workers {
				fn(i)
			}
		}()
	}
	wg.Wait()
}

// l2 平方距离（8 路累加；float32，同 faiss 的 fvec_L2sqr 精度级别）。
func l2(a, b []float32) float32 {
	var s0, s1, s2, s3, s4, s5, s6, s7 float32
	for i := 0; i+8 <= len(a); i += 8 {
		d0, d1, d2, d3 := a[i]-b[i], a[i+1]-b[i+1], a[i+2]-b[i+2], a[i+3]-b[i+3]
		d4, d5, d6, d7 := a[i+4]-b[i+4], a[i+5]-b[i+5], a[i+6]-b[i+6], a[i+7]-b[i+7]
		s0, s1, s2, s3 = s0+d0*d0, s1+d1*d1, s2+d2*d2, s3+d3*d3
		s4, s5, s6, s7 = s4+d4*d4, s5+d5*d5, s6+d6*d6, s7+d7*d7
	}
	return ((s0 + s1) + (s2 + s3)) + ((s4 + s5) + (s6 + s7))
}

func nearestCentroid(cent, v []float32) int {
	best, bi := float32(math.Inf(1)), 0
	for c := range len(cent) / cardDim {
		if d := l2(cent[c*cardDim:(c+1)*cardDim], v); d < best {
			best, bi = d, c
		}
	}
	return bi
}

// nearest 一个查询描述子的最近邻（下标、L2 平方）；IVF 时只搜最近的 nprobe 个簇。
func (db *cardIndex) nearest(q []float32) (int, float32) {
	best, bi := float32(math.Inf(1)), -1
	scan := func(i int) {
		if d := l2(db.Vecs[i*cardDim:(i+1)*cardDim], q); d < best {
			best, bi = d, i
		}
	}
	if len(db.Centroids) == 0 {
		for i := range len(db.Owner) {
			scan(i)
		}
		return bi, best
	}
	type cd struct {
		c int
		d float32
	}
	nl := len(db.Centroids) / cardDim
	ds := make([]cd, nl)
	for c := range nl {
		ds[c] = cd{c, l2(db.Centroids[c*cardDim:(c+1)*cardDim], q)}
	}
	sort.Slice(ds, func(i, j int) bool { return ds[i].d < ds[j].d || (ds[i].d == ds[j].d && ds[i].c < ds[j].c) })
	for _, x := range ds[:min(cardNProbe, nl)] {
		for _, i := range db.Lists[x.c] {
			scan(int(i))
		}
	}
	return bi, best
}

func pixelKey(b *img.BGR) uint64 {
	h := fnv.New64a()
	_, _ = h.Write(b.Pix)
	_, _ = fmt.Fprintf(h, "%dx%d", b.W, b.H)
	return h.Sum64()
}

// CardQuery 全部有票的图，按票数降序、最小距离升序（同 KAA ImageDatabase.query 的局部描述子分支）。
func CardQuery(b *img.BGR) []CardHit {
	key := pixelKey(b)
	cardMemo.Lock()
	if h, ok := cardMemo.m[key]; ok {
		cardMemo.Unlock()
		return append([]CardHit(nil), h...)
	}
	cardMemo.Unlock()
	hits := cardQuery(b)
	cardMemo.Lock()
	if len(cardMemo.order) >= cardMemoSize {
		delete(cardMemo.m, cardMemo.order[0])
		cardMemo.order = cardMemo.order[1:]
	}
	cardMemo.m[key] = hits
	cardMemo.order = append(cardMemo.order, key)
	cardMemo.Unlock()
	return append([]CardHit(nil), hits...)
}

func cardQuery(b *img.BGR) []CardHit {
	db := loadCardDB()
	if len(db.Keys) == 0 || b.Empty() {
		return []CardHit{}
	}
	q := cv.SIFT(b, cardNFeatures)
	if q.H == 0 {
		return []CardHit{}
	}
	idx := make([]int, q.H)
	dist := make([]float32, q.H)
	parallelFor(q.H, func(i int) { idx[i], dist[i] = db.nearest(q.Val[i*cardDim : (i+1)*cardDim]) })
	votes := map[int]int{}
	best := map[int]float64{}
	var order []int
	for i, ii := range idx {
		if ii < 0 || float64(dist[i]) > cardGoodDist {
			continue
		}
		k := int(db.Owner[ii])
		if _, ok := votes[k]; !ok {
			order = append(order, k)
			best[k] = math.Inf(1)
		}
		votes[k]++
		best[k] = math.Min(best[k], float64(dist[i]))
	}
	hits := make([]CardHit, 0, len(order))
	for _, k := range order {
		hits = append(hits, CardHit{db.Keys[k], votes[k], best[k]})
	}
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].Votes != hits[j].Votes {
			return hits[i].Votes > hits[j].Votes
		}
		return hits[i].Distance < hits[j].Distance
	})
	return hits
}

var charSuffix = regexp.MustCompile(`-[a-z]{4}$`)

// BaseAsset 去掉角色专属后缀（-ssmk / -hmsz …）得到 base asset_id。
func BaseAsset(asset string) string { return charSuffix.ReplaceAllString(asset, "") }

// CardMatch 卡区截图 → base asset id（角色专属图合并计票）；最高票 < minVotes 返回空串。
func CardMatch(b *img.BGR, minVotes int) string {
	votes := map[string]int{}
	best := map[string]float64{}
	var order []string
	for _, h := range CardQuery(b) {
		a := BaseAsset(h.Key)
		if _, ok := votes[a]; !ok {
			order = append(order, a)
			best[a] = math.Inf(1)
		}
		votes[a] += h.Votes
		best[a] = math.Min(best[a], h.Distance)
	}
	top := ""
	for _, a := range order { // Python min(votes, key=(-票数, 距离))：并列取先出现的
		if top == "" || votes[a] > votes[top] || (votes[a] == votes[top] && best[a] < best[top]) {
			top = a
		}
	}
	if top == "" || votes[top] < minVotes {
		return ""
	}
	return top
}
