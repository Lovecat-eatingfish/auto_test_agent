# jmeter-demo

JMeter 性能测试用的 Spring Boot 演示服务（Java 8 + Spring Boot 2.7.18），监听 **38000** 端口。
数据落在 **SQLite**（`./data/demo.db`），免装数据库服务；**JWT 登录鉴权**，业务接口需要带 token。

## 运行

```bash
# 方式一：IDEA 直接跑 PerfDemoApplication

# 方式二：命令行打 jar 后运行（建议从 jmeter-demo 目录启动，库文件相对启动目录生成）
mvn -DskipTests package
java -jar target/jmeter-demo-1.0.0.jar
```

> 注意：本机全局 Maven 配置（`D:/apache/apache-maven-3.9.6/conf/settings.xml`）强制了
> Java 17 编译级别，本工程已通过插件级显式配置压回 1.8，直接 `mvn package` 即可。

## 登录鉴权（JWT）

- 依赖：`jjwt 0.11.5`（HS256）+ `spring-security-crypto`（只引 BCrypt，不引 starter-security）。
- `POST /login` 校验用户名密码，签发 token（默认 120 分钟有效，`jwt.expire-minutes` 可调）；
  业务接口由 `JwtInterceptor` 校验 `Authorization: Bearer <token>`。
- 业务码：`4010` 用户名或密码错误、`4011` 缺少/无效 token、`4012` token 过期。
- 种子账号：`user1` ~ `user1000`，密码统一 `123456`（BCrypt 落库，演示用强度 4，生产用默认 10）。
- 密码错误与用户不存在返回同一提示，避免暴露账号是否存在。

```bash
# 登录拿 token
curl -X POST -H "Content-Type: application/json" \
  -d '{"username":"user1","password":"123456"}' http://localhost:38000/login

# 带 token 访问业务接口
curl -H "Authorization: Bearer <token>" http://localhost:38000/user/1
```

## 数据存储（SQLite）

- 依赖：`spring-boot-starter-jdbc`（JdbcTemplate + 事务）+ `org.xerial:sqlite-jdbc`。
- 库文件：`data/demo.db`，首次启动自动建表（`schema.sql`）并灌入 1000 条种子用户。
- **重置数据**：删掉 `data/demo.db`（连同 `-wal`/`-shm` 文件）后重启即可。
- 并发配置：
  - WAL 模式（启动时设置，持久在库文件里）：读接口可以并发走；
  - `PRAGMA busy_timeout = 10000`（每个连接初始化时执行）：写锁冲突时排队等锁而不是直接报 SQLITE_BUSY；
  - Hikari 池上限 10：SQLite 同一时刻只允许一个写连接，池子太大只会加剧锁竞争。
- 压测预期：**写接口（POST /order）吞吐受 SQLite 单写者限制**，加线程到一定程度 TPS 不涨、
  响应时间上涨属于正常现象；读接口可以放心加压。

表结构（`schema.sql`）：

- `users(id, username, password, name, balance)` — 种子数据 1000 条，账号 user1~user1000
- `orders(id, user_id, product, quantity, created_at)` — 由 POST /order 写入

## 接口一览

| 接口 | 鉴权 | 说明 | 压测用途 |
|---|---|---|---|
| `POST /login` | 开放 | 登录签发 JWT | 登录场景；BCrypt 校验自带 CPU 开销 |
| `GET /ping` | 开放 | 无业务逻辑，不查库 | 基准接口，测框架本身开销 |
| `GET /user/{id}` | 需要 token | 单条 SELECT | 数据库点查；id 取 1~1000 |
| `GET /users?size=100` | 需要 token | LIMIT 批量查询 | 大响应体、序列化 |
| `POST /order` | 需要 token | 写库（校验 + INSERT，语句各自自动提交） | 写路径；userId 取 1~1000 |
| `GET /slow?ms=200` | 开放 | 人为阻塞，不查库 | 线程池排队、超时 |
| `GET /stats` | 开放 | 内存计数 + `db_orders` | 压测完核对有无丢请求 |

所有接口统一返回 `{"code":0,"msg":"ok","data":...}`；业务失败返回 HTTP 200 + 业务码
（4001 参数缺失、4010 登录失败、4011/4012 token 问题、4040 用户查询不到）。

## JMeter 压测提示

**项目自带 `jmeter-demo.jmx`，已用 JMeter 5.6.3 实测跑通（6 万请求 0 错误），可直接使用：**

```bash
# GUI 打开编辑
jmeter -t jmeter-demo.jmx

# 命令行跑报告
jmeter -n -t jmeter-demo.jmx -l result.jtl -e -o report/
# 线程数/时长等默认值全部可用 -J 参数覆盖，例如加压到 200 线程跑 2 分钟：
jmeter -n -t jmeter-demo.jmx -l result.jtl -Jbusiness_threads=200 -Jbusiness_duration=120
```

计划结构（两个线程组，依次执行）：

1. `01-基准对照 /ping`：默认 20 线程 / 60 秒，纯框架开销基线；
2. `02-业务流程（登录+查询+下单）`：默认 50 线程 / 300 秒，每线程经"仅一次控制器"登录一次
   （账号 `user<线程号>`），JSON Extractor 提取 `$.data.token`，之后循环随机打
   `GET /user/{id}`、`GET /users`、`POST /order`，每个采样器带 `$.code == 0` 断言。

想自己搭计划的话，JWT 流程核心三步：

1. `POST /login` 采样器，body `{"username":"user1","password":"123456"}`，加 `Content-Type: application/json` 头；
2. 登录采样器下加 **JSON Extractor**：变量名 `token`，JSON Path `$.data.token`；
3. 业务请求加 HTTP 信息头管理器：`Authorization: Bearer ${token}`。

其他提示：

- token 默认 2 小时有效（`jwt.expire-minutes=120`），一轮压测足够；想测"每请求登录"，
  把登录采样器放进业务循环里即可（注意 BCrypt 是有意为之的 CPU 开销）。
- 想让每次请求打不同用户，用 CSV Data Set Config 给 `userId`/`username` 取值。
- 想对比线程池容量时改 `application.properties` 里的 `server.tomcat.threads.max`。
