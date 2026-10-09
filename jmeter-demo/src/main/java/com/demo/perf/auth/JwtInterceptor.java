package com.demo.perf.auth;

import com.demo.perf.exception.BizException;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/**
 * JWT 校验拦截器：要求请求头 Authorization: Bearer &lt;token&gt;，
 * 校验通过后把 userId 放进 request attribute 供后续使用。
 */
@Component
public class JwtInterceptor implements HandlerInterceptor {

    public static final String ATTR_USER_ID = "userId";

    private final JwtService jwtService;

    public JwtInterceptor(JwtService jwtService) {
        this.jwtService = jwtService;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String auth = request.getHeader("Authorization");
        if (auth == null || !auth.startsWith("Bearer ")) {
            throw new BizException(4011, "缺少 token，请先 POST /login 获取");
        }
        try {
            long userId = jwtService.parseUserId(auth.substring(7).trim());
            request.setAttribute(ATTR_USER_ID, userId);
            return true;
        } catch (ExpiredJwtException e) {
            throw new BizException(4012, "token 已过期，请重新登录");
        } catch (JwtException | IllegalArgumentException e) {
            throw new BizException(4011, "token 无效");
        }
    }
}
