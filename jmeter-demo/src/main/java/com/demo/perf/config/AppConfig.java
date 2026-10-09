package com.demo.perf.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

@Configuration
public class AppConfig {

    /**
     * 演示用 strength=4 的 BCrypt：校验快（微秒~毫秒级），
     * 既能秒级灌完 1000 条种子用户，也让登录接口的压测数字不受哈希开销支配。
     * 生产环境请用默认强度 10。
     */
    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder(4);
    }
}
