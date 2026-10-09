package com.demo.perf.controller;

import com.demo.perf.exception.BizException;
import com.demo.perf.service.OrderService;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 性能测试演示接口集，所有接口统一返回 {"code":0,"msg":"ok","data":...} 结构。
 * 数据落在 SQLite（./data/demo.db）；其中 /user、/users、/order 需要 JWT
 * （请求头 Authorization: Bearer token，见 AuthController 的 /login），
 * /ping、/slow、/stats 保持开放，方便压测对照。
 *
 * <p>几个接口的压测用途：
 * <ul>
 *   <li>/ping —— 无业务逻辑不查库，测框架本身的开销（基准接口）</li>
 *   <li>/user/{id} —— 单条 SELECT，测点查</li>
 *   <li>/users?size=100 —— LIMIT 批量查询，size 控制响应体大小</li>
 *   <li>POST /order —— 事务写库（校验 + INSERT），测写路径</li>
 *   <li>/slow?ms=200 —— 人为阻塞，测线程池排队、超时等场景</li>
 *   <li>/stats —— 内存计数器 + 库内订单总数，压测完核对有无丢请求</li>
 * </ul>
 */
@RestController
public class DemoController {

    private final JdbcTemplate jdbc;
    private final OrderService orderService;

    /** 无状态接口的请求数仍在内存累计（重启清零）；库内数据持久，重启不丢 */
    private final AtomicLong pingCount = new AtomicLong();
    private final AtomicLong userCount = new AtomicLong();
    private final AtomicLong orderCount = new AtomicLong();

    public DemoController(JdbcTemplate jdbc, OrderService orderService) {
        this.jdbc = jdbc;
        this.orderService = orderService;
    }

    /** 无业务逻辑的基准接口，不查库 */
    @GetMapping("/ping")
    public Map<String, Object> ping() {
        pingCount.incrementAndGet();
        return ok("pong");
    }

    /** 单条查询，查 SQLite，查不到返回业务码 4040 */
    @GetMapping("/user/{id}")
    public Map<String, Object> user(@PathVariable("id") long id) {
        userCount.incrementAndGet();
        try {
            return ok(jdbc.queryForMap("SELECT id, name, balance FROM users WHERE id = ?", id));
        } catch (EmptyResultDataAccessException e) {
            throw new BizException(4040, "用户不存在");
        }
    }

    /** 批量查询，size 控制响应体大小（上限 1000，正好是种子用户数） */
    @GetMapping("/users")
    public Map<String, Object> users(@RequestParam(value = "size", defaultValue = "10") int size) {
        int n = Math.min(Math.max(size, 0), 1000);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT id, name, balance FROM users ORDER BY id LIMIT ?", n);
        return ok(rows);
    }

    /**
     * 带 JSON 请求体的写接口，事务落库。
     * 请求体示例：{"userId":1,"product":"apple","quantity":3}
     */
    @PostMapping("/order")
    public Map<String, Object> order(@RequestBody Map<String, Object> body) {
        orderCount.incrementAndGet();
        Object userId = body.get("userId");
        if (!(userId instanceof Number)) {
            throw new BizException(4001, "userId 不能为空");
        }
        String product = String.valueOf(body.getOrDefault("product", "默认商品"));
        int quantity = body.get("quantity") instanceof Number
                ? ((Number) body.get("quantity")).intValue() : 1;
        return ok(orderService.createOrder(((Number) userId).longValue(), product, quantity));
    }

    /** 模拟慢接口，人为阻塞 ms 毫秒（上限 5000），不查库 */
    @GetMapping("/slow")
    public Map<String, Object> slow(@RequestParam(value = "ms", defaultValue = "200") long ms)
            throws InterruptedException {
        Thread.sleep(Math.min(Math.max(ms, 0), 5000));
        return ok("slept " + ms + "ms");
    }

    /** 各接口累计请求数 + 库内订单总数 */
    @GetMapping("/stats")
    public Map<String, Object> stats() {
        Map<String, Object> counters = new LinkedHashMap<>();
        counters.put("/ping", pingCount.get());
        counters.put("/user", userCount.get());
        counters.put("/order", orderCount.get());
        counters.put("db_orders", jdbc.queryForObject("SELECT COUNT(*) FROM orders", Long.class));
        return ok(counters);
    }

    private Map<String, Object> ok(Object data) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("code", 0);
        result.put("msg", "ok");
        result.put("data", data);
        return result;
    }
}
