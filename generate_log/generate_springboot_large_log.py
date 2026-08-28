import argparse
import datetime
import os
import random
import sys

# Windows 콘솔 환경 UTF-8 인코딩 지원 설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUTPUT_FILENAME = "springboot_test.log"
TOTAL_RECORDS = 100000

# Spring Boot 정상 로그 템플릿
LOG_TEMPLATES = [
    (
        "INFO",
        "http-nio-8080-exec-{exec_id}",
        "org.springframework.web.servlet.DispatcherServlet",
        "Completed 200 OK for request [/api/v1/orders]",
    ),
    (
        "INFO",
        "http-nio-8080-exec-{exec_id}",
        "com.example.demo.controller.OrderController",
        "Processing order request for user_id: {user_id}",
    ),
    (
        "DEBUG",
        "http-nio-8080-exec-{exec_id}",
        "com.example.demo.repository.UserRepository",
        "Fetching user profile for user_id: {user_id}",
    ),
    (
        "WARN",
        "scheduling-{exec_id}",
        "com.example.demo.repository.UserRepository",
        "Scheduled task executed successfully: CleanupTempFiles",
    ),
    (
        "DEBUG",
        "http-nio-8080-exec-{exec_id}",
        "com.zaxxer.hikari.pool.HikariPool",
        "HikariPool-1 - Pool stats (total=10, active=2, idle=8, waiting=0)",
    ),
    (
        "INFO",
        "main",
        "org.springframework.boot.SpringApplication",
        "Started DemoApplication in 2.345 seconds (JVM running for 2.89)",
    ),
    (
        "INFO",
        "http-nio-8080-exec-{exec_id}",
        "com.example.demo.service.OrderService",
        "Order created successfully for order_id: ORD-{user_id}",
    ),
    (
        "DEBUG",
        "http-nio-8080-exec-{exec_id}",
        "org.hibernate.SQL",
        "select user0_.id as id1_0_, user0_.email as email2_0_ from users user0_ where user0_.id=?",
    ),
]

# 장애 시나리오 모음
ERROR_SCENARIOS = [
    # 1. DB/Pool Lock & Timeout
    {
        "logger": "com.example.demo.repository.UserRepository",
        "type": "org.springframework.dao.CannotAcquireLockException",
        "msg": "Could not open JPA EntityManager for transaction; nested exception is org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection",
        "stack": [
            ("com.example.demo.repository.UserRepository", "findById", 45),
            ("com.example.demo.service.UserService", "getUserDetails", 82),
            ("com.example.demo.controller.UserController", "getUserInfo", 48),
        ],
        "caused_by": {
            "type": "com.zaxxer.hikari.pool.HikariPool$PoolInitializationException",
            "msg": "Exception during pool initialization: Connection is not available, request timed out after 30000ms.",
            "stack": [
                ("com.example.demo.repository.UserRepository", "findById", 26),
                ("com.example.demo.service.UserService", "getUserDetails", 12),
            ],
        },
    },
    # 2. 외부 REST API 타임아웃
    {
        "logger": "com.example.demo.service.PaymentService",
        "type": "org.springframework.web.client.ResourceAccessException",
        "msg": 'I/O error on POST request for "https://api.external-pg.com/v1/payments": Read timed out',
        "stack": [
            ("com.example.demo.service.PaymentService", "processPayment", 146),
            ("com.example.demo.controller.OrderController", "createOrder", 29),
        ],
        "caused_by": {
            "type": "java.net.SocketTimeoutException",
            "msg": "Read timed out at java.net.SocketInputStream.socketRead0",
            "stack": [
                ("com.example.demo.service.PaymentService", "processPayment", 18),
                ("com.example.demo.controller.OrderController", "createOrder", 27),
            ],
        },
    },
    # 3. JWT 만료 / 인증 에러
    {
        "logger": "com.zaxxer.hikari.pool.HikariPool",
        "type": "io.jsonwebtoken.ExpiredJwtException",
        "msg": "JWT expired at 2026-07-26T17:00:00Z. Current time: 2026-07-26T17:30:00Z",
        "stack": [
            ("com.example.demo.security.JwtTokenProvider", "validateToken", 67),
            ("com.example.demo.security.JwtAuthenticationFilter", "doFilterInternal", 66),
        ],
        "caused_by": None,
    },
    # 4. NullPointer Exception
    {
        "logger": "com.example.demo.service.PaymentService",
        "type": "java.lang.NullPointerException",
        "msg": 'Cannot invoke "com.example.demo.domain.User.getEmail()" because "user" is null',
        "stack": [
            ("com.example.demo.service.NotificationService", "sendEmailNotice", 83),
            ("com.example.demo.service.OrderService", "completeOrder", 44),
            ("com.example.demo.controller.OrderController", "createOrder", 132),
        ],
        "caused_by": None,
    },
    # 5. Redis / Cache 커넥션 에러
    {
        "logger": "com.example.demo.service.CacheService",
        "type": "org.springframework.data.redis.RedisConnectionFailureException",
        "msg": "Unable to connect to Redis; nested exception is io.lettuce.core.RedisCommandTimeoutException: Command timed out after 3 second(s)",
        "stack": [
            ("com.example.demo.service.CacheService", "getOrSetCache", 54),
            ("com.example.demo.service.UserService", "getUserProfile", 102),
        ],
        "caused_by": {
            "type": "io.lettuce.core.RedisCommandTimeoutException",
            "msg": "Command timed out after 3 second(s)",
            "stack": [
                ("io.lettuce.core.RedisChannelHandler", "dispatch", 210),
            ],
        },
    },
    # 6. OutOfMemoryError
    {
        "logger": "com.example.demo.service.BatchReportService",
        "type": "java.lang.OutOfMemoryError",
        "msg": "Java heap space",
        "stack": [
            ("com.example.demo.service.BatchReportService", "generateMonthlyReport", 215),
            ("com.example.demo.controller.AdminController", "triggerBatchJob", 88),
        ],
        "caused_by": None,
    },
]


def generate_springboot_logs(
    output_filename: str = OUTPUT_FILENAME,
    total_records: int = TOTAL_RECORDS,
    error_rate: float = 0.12,
):
    """Spring Boot 2.x / 3.x 표준 대용량 로그 파일 생성 함수"""
    print(f"[START] Spring Boot 대용량 테스트 로그 생성 시작: {output_filename} ({total_records:,}건)")

    dir_name = os.path.dirname(output_filename)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    start_time = datetime.datetime(2026, 8, 13, 9, 0, 0)
    current_time = start_time
    pid = 12345

    with open(output_filename, "w", encoding="utf-8") as f:
        for idx in range(1, total_records + 1):
            current_time += datetime.timedelta(milliseconds=random.randint(20, 300))
            timestamp_str = (
                current_time.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{random.randint(100, 999):03d}+09:00"
            )
            exec_id = random.randint(1, 20)
            user_id = random.randint(1000, 9999)

            if random.random() < error_rate:
                scenario = random.choice(ERROR_SCENARIOS)
                thread_prefix = random.choice(["http-nio-8080-exec-", "scheduling-"])
                f.write(
                    f"{timestamp_str}  ERROR {pid} --- [{thread_prefix}{exec_id}] "
                    f"{scenario['logger']} : {scenario['type']}: {scenario['msg']}\n"
                )
                for cls, method, line_num in scenario["stack"]:
                    f.write(f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{line_num})\n")

                if scenario.get("caused_by"):
                    cb = scenario["caused_by"]
                    f.write(f"Caused by: {cb['type']}: {cb['msg']}\n")
                    if "stack" in cb and cb["stack"]:
                        for cls, method, line_num in cb["stack"]:
                            f.write(f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{line_num})\n")
            else:
                level, thread_tmpl, logger, msg_tmpl = random.choice(LOG_TEMPLATES)
                thread = thread_tmpl.format(exec_id=exec_id)
                msg = msg_tmpl.format(user_id=user_id)
                f.write(f"{timestamp_str}  {level:<5} {pid} --- [{thread}] {logger} : {msg}\n")

            if idx % 20000 == 0 or idx == total_records:
                pct = (idx / total_records) * 100
                print(f"  └─ 진행률: {idx:,} / {total_records:,} 건 ({pct:.1f}%)")

    file_size_mb = os.path.getsize(output_filename) / (1024 * 1024)
    print(f"[SUCCESS] Spring Boot 로그 생성 완료: {output_filename} ({file_size_mb:.2f} MB)")


def main():
    parser = argparse.ArgumentParser(description="Spring Boot 대용량 테스트 로그 파일 생성기")
    parser.add_argument(
        "-o",
        "--output",
        default=OUTPUT_FILENAME,
        help=f"출력 파일 경로 (기본값: {OUTPUT_FILENAME})",
    )
    parser.add_argument(
        "-c",
        "--count",
        type=int,
        default=TOTAL_RECORDS,
        help=f"생성할 레코드 수 (기본값: {TOTAL_RECORDS})",
    )
    parser.add_argument(
        "-e", "--error-rate", type=float, default=0.12, help="에러 비율 (기본값: 0.12)"
    )
    args = parser.parse_args()

    generate_springboot_logs(
        output_filename=args.output,
        total_records=args.count,
        error_rate=args.error_rate,
    )


if __name__ == "__main__":
    main()
