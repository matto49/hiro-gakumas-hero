//go:build windows

package device

import "net"

// peerAlive：MaaTools 只在 Mac 上用；Windows 上不探测，写失败时照样会重连。
func peerAlive(net.Conn) bool { return true }
