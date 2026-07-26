import random
import time
from datetime import datetime, timedelta

# 생성할 로그 파일명 및 라인 수
OUTPUT_FILE = "springboot_test.log"
TOTAL_LOG_COUNT = 300

# 더미 데이터 모음
LOG_LEVELS = ["INFO", "INFO", "INFO", "WARN", "DEBUG", "ERROR"]
THREADS = [
    "http-nio-8080-exec-1",
    "http-nio-8080-exec-2",
    "http-nio-8080-exec-3",
    "scheduling-1",
    "task-executor-1",
]
LOGGERS = [
    "org.springframework.web.servlet.DispatcherServlet",
    "com.example.demo.controller.OrderController",
    "com.example.demo.service.PaymentService",
    "com.example.demo.repository.UserRepository",
    "com.zaxxer.hikari.pool.HikariPool",
    "org.hibernate.engine.jdbc.spi.SqlExceptionHelper",
]

# 정상 INFO 메시지 예시
INFO_MESSAGES = [
    "Initializing Servlet 'dispatcherServlet'",
    "Completed 200 OK for request [/api/v1/orders]",
    "Fetching user profile from database for user_id: 1042",
    "Scheduled task executed successfully: CleanupTempFiles",
    "User authentication success: user@example.com",
]

# 발생시킬 주요 장애 예시 세트 (유형별)
ERROR_SCENARIOS = [
    # 1. Database 장애 (HikariCP / SQL Timeout)
    {
        "type": "org.springframework.dao.CannotAcquireLockException",
        "msg": "Could not open JPA EntityManager for transaction; nested exception is org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection",
        "stack": [
            ("com.example.demo.repository.UserRepository", "findById"),
            ("com.example.demo.service.UserService", "getUserDetails"),
            ("com.example.demo.controller.UserController", "getUserInfo"),
        ],
        "caused_by": {
            "type": "com.zaxxer.hikari.pool.HikariPool$PoolInitializationException",
            "msg": "Exception during pool initialization: Connection is not available, request timed out after 30000ms.",
        },
    },
    # 2. Network / External API 장애
    {
        "type": "org.springframework.web.client.ResourceAccessException",
        "msg": 'I/O error on POST request for "https://api.external-pg.com/v1/payments": Read timed out',
        "stack": [
            ("com.example.demo.service.PaymentService", "processPayment"),
            ("com.example.demo.controller.OrderController", "createOrder"),
        ],
        "caused_by": {
            "type": "java.net.SocketTimeoutException",
            "msg": "Read timed out at java.net.SocketInputStream.socketRead0",
        },
    },
    # 3. Auth / Security 장애
    {
        "type": "io.jsonwebtoken.ExpiredJwtException",
        "msg": "JWT expired at 2026-07-26T17:00:00Z. Current time: 2026-07-26T17:30:00Z",
        "stack": [
            ("com.example.demo.security.JwtTokenProvider", "validateToken"),
            ("com.example.demo.security.JwtAuthenticationFilter", "doFilterInternal"),
        ],
        "caused_by": None,
    },
    # 4. Application Logic (NullPointer 등)
    {
        "type": "java.lang.NullPointerException",
        "msg": 'Cannot invoke "com.example.demo.domain.User.getEmail()" because "user" is null',
        "stack": [
            ("com.example.demo.service.NotificationService", "sendEmailNotice"),
            ("com.example.demo.service.OrderService", "completeOrder"),
            ("com.example.demo.controller.OrderController", "createOrder"),
        ],
        "caused_by": None,
    },
]


def generate_dummy_logs():
    current_time = datetime.now() - timedelta(hours=2)  # 2시간 전부터 로그 시작
    pid = 12345

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        print(f"Generating Spring Boot logs -> '{OUTPUT_FILE}'...")

        for _ in range(TOTAL_LOG_COUNT):
            # 시간 약간씩 증가 (1초~10초)
            current_time += timedelta(seconds=random.randint(1, 10))

            # Spring Boot ISO 8601 타임스탬프 포맷
            timestamp_str = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+09:00"

            level = random.choice(LOG_LEVELS)
            thread = random.choice(THREADS)
            logger = random.choice(LOGGERS)

            if level != "ERROR":
                # 일반 INFO / DEBUG 로그
                msg = random.choice(INFO_MESSAGES)
                log_line = (
                    f"{timestamp_str}  {level:<5} {pid} --- [{thread}] {logger:<40} : {msg}\n"
                )
                f.write(log_line)
            else:
                # ERROR 로그 및 예외 스택트레이스 생성
                scenario = random.choice(ERROR_SCENARIOS)
                ex_type = scenario["type"]
                ex_msg = scenario["msg"]

                log_line = f"{timestamp_str}  ERROR {pid} --- [{thread}] {logger:<40} : {ex_type}: {ex_msg}\n"
                f.write(log_line)

                # StackTrace 작성 (\tat 구문)
                f.writelines(
                    f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{random.randint(20, 150)})\n"
                    for cls, method in scenario["stack"]
                )

                # Caused by 작성
                if scenario["caused_by"]:
                    c_type = scenario["caused_by"]["type"]
                    c_msg = scenario["caused_by"]["msg"]
                    f.write(f"Caused by: {c_type}: {c_msg}\n")
                    # 하위 스택 1~2개
                    f.writelines(
                        f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{random.randint(10, 50)})\n"
                        for cls, method in scenario["stack"][:2]
                    )

    print(f"✅ Successful! File generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_dummy_logs()
