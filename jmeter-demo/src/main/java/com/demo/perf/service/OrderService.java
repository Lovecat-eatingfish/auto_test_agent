package com.demo.perf.service;

import com.demo.perf.exception.BizException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 订单写路径：用户存在性校验 + 插入订单。
 *
 * <p>刻意不加 @Transactional：SQLite WAL 模式下"读事务升级为写事务"时，
 * 若快照之后已有别的连接提交过写入，会立即抛 SQLITE_BUSY（BUSY_SNAPSHOT，
 * 不走 busy_timeout），压测时表现为零星 500。这里每条语句各自自动提交，
 * 写锁冲突交给 busy_timeout 排队，才是 SQLite 并发写的正确姿势。
 */
@Service
public class OrderService {

    private final JdbcTemplate jdbc;

    public OrderService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Map<String, Object> createOrder(long userId, String product, int quantity) {
        Integer userExists = jdbc.queryForObject(
                "SELECT COUNT(*) FROM users WHERE id = ?", Integer.class, userId);
        if (userExists == null || userExists == 0) {
            throw new BizException(4002, "用户不存在");
        }

        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbc.update(con -> {
            PreparedStatement ps = con.prepareStatement(
                    "INSERT INTO orders(user_id, product, quantity) VALUES (?, ?, ?)",
                    Statement.RETURN_GENERATED_KEYS);
            ps.setLong(1, userId);
            ps.setString(2, product);
            ps.setInt(3, quantity);
            return ps;
        }, keyHolder);

        Map<String, Object> order = new LinkedHashMap<>();
        order.put("orderId", keyHolder.getKey() == null ? null : keyHolder.getKey().longValue());
        order.put("userId", userId);
        order.put("product", product);
        order.put("quantity", quantity);
        return order;
    }
}
