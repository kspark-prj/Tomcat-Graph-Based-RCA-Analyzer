import datetime
import os
import random

# 생성할 파일명 및 목표 줄 수 설정
OUTPUT_FILENAME = "application_huge.log"
TOTAL_RECORDS = 100000  # 약 10만 건 생성 (필요에 따라 늘리거나 줄일 수 있습니다)

# ---------------------------------------------------------
# 1. 정상 비즈니스 / 인프라 로그 템플릿 모음
# ---------------------------------------------------------
LOG_TEMPLATES = [
    (
        "INFO",
        "http-nio-8080-exec-{exec_id}",
        "org.springframework.web.servlet.DispatcherServlet",
        "Initializing Servlet 'dispatcherServlet'",
    ),
    (
        "INFO",
        "http-nio-8080-exec-{exec_id}",
        "com.example.demo.controller.OrderController",
        "Completed 200 OK for request [/api/v1/orders]",
    ),
    (
        "DEBUG",
        "http-nio-8080-exec-{exec_id}",
        "com.example.demo.repository.UserRepository",
        "Fetching user profile from database for user_id: {user_id}",
    ),
    (
        "INFO",
        "scheduling-1",
        "com.example.demo.task.CleanupTask",
        "Scheduled task executed successfully: CleanupTempFiles",
    ),
    (
        "DEBUG",
        "http-nio-8080-exec-{exec_id}",
        "com.zaxxer.hikari.pool.HikariPool",
        "User authentication success: user@{domain}",
    ),
    (
        "WARN",
        "http-nio-8080-exec-{exec_id}",
        "org.hibernate.engine.jdbc.spi.SqlExceptionHelper",
        "Slow query detected: SELECT * FROM orders WHERE user_id = {user_id} (execution time: 1250ms)",
    ),
]

# ---------------------------------------------------------
# 2. 다각화된 4대 핵심 장애 시나리오 세트 (RCA 분석용)
# ---------------------------------------------------------
ERROR_SCENARIOS = [
    # 시나리오 A: [DB/Pool 병목] HikariCP Connection Timeout & JPA Lock Exception
    {
        "logger": "com.zaxxer.hikari.pool.HikariPool",
        "type": "org.springframework.dao.CannotAcquireLockException",
        "msg": "Could not open JPA EntityManager for transaction; nested exception is org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection",
        "stack": [
            ("com.example.demo.repository.UserRepository", "findById", 45),
            ("com.example.demo.service.UserService", "getUserDetails", 112),
            ("com.example.demo.controller.UserController", "getUserInfo", 34),
        ],
        "caused_by": {
            "type": "com.zaxxer.hikari.pool.HikariPool$PoolInitializationException",
            "msg": "Exception during pool initialization: Connection is not available, request timed out after 30000ms.",
            "stack": [
                ("com.zaxxer.hikari.pool.HikariPool", "getConnection", 162),
                ("com.zaxxer.hikari.HikariDataSource", "getConnection", 100),
            ],
        },
    },
    # 시나리오 B: [외부 통신/네트워크 타임아웃] PG사 결제 연동 Read Timeout
    {
        "logger": "com.example.demo.service.PaymentService",
        "type": "org.springframework.web.client.ResourceAccessException",
        "msg": 'I/O error on POST request for "https://api.external-pg.com/v1/payments": Read timed out',
        "stack": [
            ("com.example.demo.service.PaymentService", "processPayment", 146),
            ("com.example.demo.service.OrderService", "checkout", 88),
            ("com.example.demo.controller.OrderController", "createOrder", 29),
        ],
        "caused_by": {
            "type": "java.net.SocketTimeoutException",
            "msg": "Read timed out at java.net.SocketInputStream.socketRead0(Native Method)",
            "stack": [
                ("java.net.SocketInputStream", "read", 150),
                ("org.apache.http.impl.io.SessionInputBufferImpl", "read", 280),
            ],
        },
    },
    # 시나리오 C: [보안 및 인증] JWT 토큰 만료 (ExpiredJwtException)
    {
        "logger": "com.example.demo.security.JwtAuthenticationFilter",
        "type": "io.jsonwebtoken.ExpiredJwtException",
        "msg": "JWT expired at 2026-08-13T10:00:00Z. Current time: 2026-08-13T10:30:00Z",
        "stack": [
            ("com.example.demo.security.JwtTokenProvider", "validateToken", 67),
            ("com.example.demo.security.JwtAuthenticationFilter", "doFilterInternal", 66),
            ("org.springframework.web.filter.OncePerRequestFilter", "doFilter", 119),
        ],
        "caused_by": None,
    },
    # 시나리오 D: [애플리케이션 로직 결함] NullPointerException
    {
        "logger": "com.example.demo.service.NotificationService",
        "type": "java.lang.NullPointerException",
        "msg": 'Cannot invoke "com.example.demo.domain.User.getEmail()" because "user" is null',
        "stack": [
            ("com.example.demo.service.NotificationService", "sendEmailNotice", 52),
            ("com.example.demo.service.OrderService", "completeOrder", 204),
            ("com.example.demo.controller.OrderController", "createOrder", 35),
        ],
        "caused_by": None,
    },
]

DOMAINS = ["example.com", "test.org", "company.net", "service.io"]


def generate_logs():
    print(f"🚀 대용량 테스트 로그 파일 생성 시작: {OUTPUT_FILENAME} ({TOTAL_RECORDS:,} 레코드)")

    start_time = datetime.datetime(2026, 8, 13, 9, 0, 0)
    current_time = start_time
    pid = 12345

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        for idx in range(TOTAL_RECORDS):
            # 시간 점진적 증가 (50ms ~ 500ms)
            current_time += datetime.timedelta(milliseconds=random.randint(50, 500))

            # Spring Boot 및 WAS 파서 호환 표준 ISO-8601 타임스탬프
            timestamp_str = current_time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{random.randint(100, 999)}+09:00"
            exec_id = random.randint(1, 20)
            user_id = random.randint(1000, 9999)
            domain = random.choice(DOMAINS)

            # 12% 확률로 심각한 장애(ERROR) 스택트레이스 발생
            if random.random() < 0.12:
                scenario = random.choice(ERROR_SCENARIOS)
                logger = scenario["logger"]
                thread = f"http-nio-8080-exec-{exec_id}"
                ex_type = scenario["type"]
                ex_msg = scenario["msg"]

                # 1. Error 헤더 줄 작성
                header_line = f"{timestamp_str}  ERROR {pid} --- [{thread}] {logger:<50} : {ex_type}: {ex_msg}\n"
                f.write(header_line)

                # 2. Main StackTrace (\tat 구문) 작성
                for cls, method, line_num in scenario["stack"]:
                    file_name = cls.split(".")[-1] + ".java"
                    f.write(f"\tat {cls}.{method}({file_name}:{line_num})\n")

                # 3. Caused by 구문 작성 (존재 시)
                if scenario["caused_by"]:
                    cb = scenario["caused_by"]
                    f.write(f"Caused by: {cb['type']}: {cb['msg']}\n")
                    for cls, method, line_num in cb["stack"]:
                        file_name = cls.split(".")[-1] + ".java"
                        f.write(f"\tat {cls}.{method}({file_name}:{line_num})\n")
            else:
                # 일반 정상 / 경고 로그 작성
                level, thread_tmpl, logger, msg_tmpl = random.choice(LOG_TEMPLATES)
                thread = thread_tmpl.format(exec_id=exec_id)
                msg = msg_tmpl.format(exec_id=exec_id, user_id=user_id, domain=domain)

                f.write(f"{timestamp_str}  {level:<5} {pid} --- [{thread}] {logger:<50} : {msg}\n")

            # 진행률 표시 (매 5만 건 마다)
            if (idx + 1) % 50000 == 0:
                print(f" ⏳ 진행 상황: {idx + 1:,} / {TOTAL_RECORDS:,} 건 완료...")

    file_size_mb = os.path.getsize(OUTPUT_FILENAME) / (1024 * 1024)
    print(f"✅ 생성 완료! 파일명: {OUTPUT_FILENAME} (용량: {file_size_mb:.2f} MB)")


if __name__ == "__main__":
    generate_logs()
