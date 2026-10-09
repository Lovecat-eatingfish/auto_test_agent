package com.demo.perf.controller;

import com.demo.perf.auth.JwtService;
import com.demo.perf.exception.BizException;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 登录接口：校验用户名密码（BCrypt），通过后签发 JWT。
 * 统一返回 {"code":0,"msg":"ok","data":...}；用户名或密码错误返回业务码 4010。
 */
@RestController
public class AuthController {

    private final JdbcTemplate jdbc;
    private final JwtService jwtService;
    private final PasswordEncoder passwordEncoder;

    public AuthController(JdbcTemplate jdbc, JwtService jwtService, PasswordEncoder passwordEncoder) {
        this.jdbc = jdbc;
        this.jwtService = jwtService;
        this.passwordEncoder = passwordEncoder;
    }

    /**
     * 请求体示例：{"username":"user1","password":"123456"}
     * 成功返回 data: {token, tokenType, expiresIn, userId, username}
     */
    @PostMapping("/login")
    public Map<String, Object> login(@RequestBody Map<String, Object> body) {
        Object username = body.get("username");
        Object password = body.get("password");
        if (username == null || password == null) {
            throw new BizException(4001, "username 和 password 不能为空");
        }

        Map<String, Object> row;
        try {
            row = jdbc.queryForMap("SELECT id, username, password FROM users WHERE username = ?",
                    String.valueOf(username));
        } catch (EmptyResultDataAccessException e) {
            // 用户不存在与密码错误返回同一个提示，避免暴露用户名是否存在
            throw new BizException(4010, "用户名或密码错误");
        }
        if (!passwordEncoder.matches(String.valueOf(password), String.valueOf(row.get("password")))) {
            throw new BizException(4010, "用户名或密码错误");
        }

        long userId = ((Number) row.get("id")).longValue();
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("token", jwtService.generate(userId, String.valueOf(row.get("username"))));
        data.put("tokenType", "Bearer");
        data.put("expiresIn", jwtService.getExpireSeconds());
        data.put("userId", userId);
        data.put("username", row.get("username"));
        return ok(data);
    }

    private Map<String, Object> ok(Object data) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("code", 0);
        result.put("msg", "ok");
        result.put("data", data);
        return result;
    }
}
