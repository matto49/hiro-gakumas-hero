// Package clierr 是 hiro 命令的错误：code 给程序判断，message / hint 用白话写给人看，同 Python 版 CliError。
package clierr

import "errors"

// Error 是命令失败。Exit 是退出码（缺省 1；用法错误 2）。
type Error struct {
	Code    string
	Message string
	Hint    string
	Exit    int
}

func (e *Error) Error() string { return e.Message }

// New 建一个退出码 1 的错误。
func New(code, message, hint string) *Error {
	return &Error{Code: code, Message: message, Hint: hint, Exit: 1}
}

// Usage 建一个退出码 2（用法错误）的错误。
func Usage(code, message, hint string) *Error {
	return &Error{Code: code, Message: message, Hint: hint, Exit: 2}
}

// As 把任意错误转成 *Error；不是的话包成 code=internal。
func As(err error) *Error {
	var e *Error
	if errors.As(err, &e) {
		return e
	}
	return &Error{Code: "internal", Message: err.Error(), Hint: "重试；一直这样请反馈", Exit: 1}
}
