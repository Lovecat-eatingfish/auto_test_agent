package com.demo.perf.auth;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/**
 * JWT 签发与校验（HS256）。token 的 subject 存 userId，另带 username claim。
 */
@Component
public class JwtService {

    @Value("${jwt.secret}")
    private String secret;

    @Value("${jwt.expire-minutes:120}")
    private long expireMinutes;

    /** 签发 token */
    public String generate(long userId, String username) {
        Date now = new Date();
        return Jwts.builder()
                .setSubject(String.valueOf(userId))
                .claim("username", username)
                .setIssuedAt(now)
                .setExpiration(new Date(now.getTime() + expireMinutes * 60_000))
                .signWith(signingKey(), SignatureAlgorithm.HS256)
                .compact();
    }

    /** 校验并解析 token，返回 userId；无效或过期时抛 JwtException（含 ExpiredJwtException） */
    public long parseUserId(String token) {
        Claims claims = Jwts.parserBuilder()
                .setSigningKey(signingKey())
                .build()
                .parseClaimsJws(token)
                .getBody();
        return Long.parseLong(claims.getSubject());
    }

    /** 有效期秒数，登录接口响应里回给调用方 */
    public long getExpireSeconds() {
        return expireMinutes * 60;
    }

    private SecretKey signingKey() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }
}
