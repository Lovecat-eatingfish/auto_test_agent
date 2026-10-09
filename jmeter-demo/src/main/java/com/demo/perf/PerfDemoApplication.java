package com.demo.perf;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * JMeter 性能测试演示服务，启动后监听 38000 端口。
 */
@SpringBootApplication
public class PerfDemoApplication {

    public static void main(String[] args) {
        SpringApplication.run(PerfDemoApplication.class, args);
    }
}
