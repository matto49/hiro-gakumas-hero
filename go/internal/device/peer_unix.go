//go:build !windows

package device

import (
	"errors"
	"net"

	"golang.org/x/sys/unix"
)

// peerAlive：TUCH 没有回包，对端断开后写往往仍「成功」、触摸被静默丢掉；发送前用非阻塞 peek 探测对端是否已关闭。
func peerAlive(conn net.Conn) bool {
	tc, ok := conn.(*net.TCPConn)
	if !ok {
		return true
	}
	raw, err := tc.SyscallConn()
	if err != nil {
		return false
	}
	alive := true
	err = raw.Read(func(fd uintptr) bool {
		buf := make([]byte, 1)
		n, _, rerr := unix.Recvfrom(int(fd), buf, unix.MSG_PEEK|unix.MSG_DONTWAIT)
		switch {
		case errors.Is(rerr, unix.EAGAIN) || errors.Is(rerr, unix.EWOULDBLOCK):
			alive = true // 没有数据可读：连接还在
		case rerr != nil:
			alive = false
		default:
			alive = n > 0 // 读到 0 字节 = 对端关了
		}
		return true // 只探测一次，不等
	})
	return err == nil && alive
}
