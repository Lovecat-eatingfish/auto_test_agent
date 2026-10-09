package com.demo.perf.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 启动初始化：
 * 1. 打开 WAL 模式（持久化在库文件里，设置一次即可），提升并发读性能；
 * 2. users 表为空时批量灌入种子用户，密码统一 BCrypt 哈希后落库。
 */
@Component
public class DataInitializer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(DataInitializer.class);

    /** 种子用户数，压测 GET /user/{id} 时 id 取值范围 1~1000 */
    private static final int SEED_USERS = 1000;

    /** 种子用户统一密码，登录接口用 */
    private static final String SEED_PASSWORD = "123456";

    private final JdbcTemplate jdbc;
    private final PasswordEncoder passwordEncoder;

    public DataInitializer(JdbcTemplate jdbc, PasswordEncoder passwordEncoder) {
        this.jdbc = jdbc;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    public void run(ApplicationArguments args) {
        jdbc.execute("PRAGMA journal_mode=WAL");

        Long count = jdbc.queryForObject("SELECT COUNT(*) FROM users", Long.class);
        if (count == null || count == 0) {
            long start = System.currentTimeMillis();
            List<Object[]> batch = new ArrayList<>(SEED_USERS);
            for (int i = 1; i <= SEED_USERS; i++) {
                batch.add(new Object[]{
                        "user" + i, passwordEncoder.encode(SEED_PASSWORD), "用户" + i, 100.0});
            }
            jdbc.batchUpdate("INSERT INTO users(username, password, name, balance) VALUES (?, ?, ?, ?)",
                    batch);
            log.info("users 表为空，已初始化 {} 条种子用户（账号 user1~user{}，密码 {}），耗时 {} ms",
                    SEED_USERS, SEED_USERS, SEED_PASSWORD, System.currentTimeMillis() - start);
        }
    }
}
