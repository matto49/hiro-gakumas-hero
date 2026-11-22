package cv

import (
	"gocv.io/x/gocv"

	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
)

// SIFT 描述子（cv2.SIFT_create(nfeatures=n).detectAndCompute(img, None)），每行 128 维；没有特征点返回 0 行。
func SIFT(b *img.BGR, nfeatures int) *F32 {
	m := must(bgrMat(b))
	defer m.Close()
	s := gocv.NewSIFTWithParams(&nfeatures, nil, nil, nil, nil)
	defer func() { _ = s.Close() }()
	mask := gocv.NewMat()
	defer mask.Close()
	_, d := s.DetectAndCompute(m, mask)
	defer d.Close()
	if d.Empty() {
		return &F32{W: 128}
	}
	out := &F32{W: d.Cols(), H: d.Rows(), Val: make([]float32, d.Cols()*d.Rows())}
	v, _ := d.DataPtrFloat32()
	copy(out.Val, v)
	return out
}

// KMeans 聚类（cv2.kmeans，随机初始中心，OpenCV 的随机数在进程内固定种子），返回 k 个中心（每行一个）。
func KMeans(data *F32, k, iterations int) *F32 {
	m := f32Mat(data)
	defer m.Close()
	labels := gocv.NewMat()
	defer labels.Close()
	centers := gocv.NewMat()
	defer centers.Close()
	crit := gocv.NewTermCriteria(gocv.MaxIter, iterations, 0)
	gocv.KMeans(m, k, &labels, crit, 1, gocv.KMeansRandomCenters, &centers)
	out := &F32{W: centers.Cols(), H: centers.Rows(), Val: make([]float32, centers.Cols()*centers.Rows())}
	v, _ := centers.DataPtrFloat32()
	copy(out.Val, v)
	return out
}
