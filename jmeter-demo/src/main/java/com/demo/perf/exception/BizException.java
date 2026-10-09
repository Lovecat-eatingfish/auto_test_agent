package com.demo.perf.exception;

/**
 * 业务异常，由 GlobalExceptionHandler 统一转成 fail 信封。
 */
public class BizException extends RuntimeException {

    private final int code;

    public BizException(int code, String msg) {
        super(msg);
        this.code = code;
    }

    public int getCode() {
        return code;
    }
}
