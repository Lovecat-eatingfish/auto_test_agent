package com.demo.perf.exception;

import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 统一异常处理：业务失败也返回 HTTP 200 + 业务码，保持 {"code","msg","data"} 信封一致。
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BizException.class)
    public Map<String, Object> handleBiz(BizException e) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("code", e.getCode());
        result.put("msg", e.getMessage());
        result.put("data", null);
        return result;
    }
}
