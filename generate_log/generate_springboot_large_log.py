import datetime
import random

# 생성할 파일명 및 목표 줄 수 설정
OUTPUT_FILENAME = "application_huge.log"
TOTAL_RECORDS = 500000  # 원하는 데이터 건수 (약 50만 건 -> ~100MB)

log_templates = [
    (
        "WARN",
        "[scheduling-1]",
        "com.example.demo.repository.UserRepository",
        "Scheduled task executed successfully: CleanupTempFiles",
    ),
    (
        "DEBUG",
        "[http-nio-8080-exec-{exec_id}]",
        "com.zaxxer.hikari.pool.HikariPool",
        "User authentication success: user@{domain}",
    ),
    (
        "INFO",
        "[http-nio-8080-exec-{exec_id}]",
        "com.zaxxer.hikari.pool.HikariPool",
        "User authentication success: user@{domain}",
    ),
    (
        "WARN",
        "[http-nio-8080-exec-{exec_id}]",
        "org.hibernate.engine.jdbc.spi.SqlExceptionHelper",
        "Fetching user profile from database for user_id: {user_id}",
    ),
    (
        "DEBUG",
        "[http-nio-8080-exec-{exec_id}]",
        "com.example.demo.service.PaymentService",
        "Scheduled task executed successfully: CleanupTempFiles",
    ),
    (
        "INFO",
        "[http-nio-8080-exec-{exec_id}]",
        "com.example.demo.controller.OrderController",
        "Completed 200 OK for request [/api/v1/orders]",
    ),
]

error_templates = [
    (
        "ERROR",
        "[http-nio-8080-exec-{exec_id}]",
        "com.zaxxer.hikari.pool.HikariPool",
        "io.jsonwebtoken.ExpiredJwtException: JWT expired at 2026-07-26T17:00:00Z. Current time: 2026-07-26T17:30:00Z\n\tat com.example.demo.security.JwtTokenProvider.validateToken(JwtTokenProvider.java:67)\n\tat com.example.demo.security.JwtAuthenticationFilter.doFilterInternal(JwtAuthenticationFilter.java:66)",
    ),
    (
        "ERROR",
        "[http-nio-8080-exec-{exec_id}]",
        "com.example.demo.service.PaymentService",
        'org.springframework.web.client.ResourceAccessException: I/O error on POST request for "https://api.external-pg.com/v1/payments": Read timed out\n\tat com.example.demo.service.PaymentService.processPayment(PaymentService.java:146)\n\tat com.example.demo.controller.OrderController.createOrder(OrderController.java:29)',
    ),
]

domains = ["example.com", "test.org", "company.net", "service.io"]
start_time = datetime.datetime(2026, 7, 26, 15, 40, 25)

with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
    current_time = start_time
    for _ in range(TOTAL_RECORDS):
        current_time += datetime.timedelta(milliseconds=random.randint(100, 2000))
        timestamp_str = (
            current_time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{random.randint(100, 999)}+09:00"
        )
        exec_id = random.randint(1, 10)
        user_id = random.randint(1000, 9999)
        domain = random.choice(domains)

        if random.random() < 0.15:  # 15% 확률로 에러 발생
            level, thread_tmpl, logger, msg_tmpl = random.choice(error_templates)
        else:
            level, thread_tmpl, logger, msg_tmpl = random.choice(log_templates)

        thread = thread_tmpl.format(exec_id=exec_id)
        msg = msg_tmpl.format(exec_id=exec_id, user_id=user_id, domain=domain)
        f.write(f"{timestamp_str}  {level:<5} 12345 --- {thread:<23} {logger:<50} : {msg}\n")
